"""Health checks. Every check is read-only and tolerant of command failure."""

from __future__ import annotations

import json
import plistlib
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional, Sequence

from .models import CheckResult, Outcome, Status, outcome
from .runner import CommandResult

# Absolute paths only: never rely on the caller's PATH.
SW_VERS = "/usr/bin/sw_vers"
SYSCTL = "/usr/sbin/sysctl"
UNAME = "/usr/bin/uname"
DF = "/bin/df"
MEMORY_PRESSURE = "/usr/bin/memory_pressure"
SYSTEM_PROFILER = "/usr/sbin/system_profiler"
DISKUTIL = "/usr/sbin/diskutil"
FIND = "/usr/bin/find"
PROFILES = "/usr/bin/profiles"

DIAGNOSTIC_REPORTS = "/Library/Logs/DiagnosticReports"

# Tunable thresholds. Change these rather than editing check logic.
THRESHOLDS = {
    "uptime_warn_days": 14,
    "uptime_fail_days": 30,
    "disk_warn_free_gb": 25.0,
    "disk_fail_free_gb": 10.0,
    "disk_warn_free_pct": 15.0,
    "disk_fail_free_pct": 5.0,
    "memory_warn_free_pct": 25,
    "memory_fail_free_pct": 10,
    "load_warn_per_core": 1.0,
    "load_fail_per_core": 2.0,
    "battery_warn_capacity_pct": 80,
    "panic_lookback_days": 7,
    "panic_fail_count": 3,
}


@dataclass
class Context:
    runner: Any
    now: datetime


CheckFn = Callable[[Context], Outcome]
_REGISTRY: List[tuple] = []


def check(check_id: str, title: str, category: str) -> Callable[[CheckFn], CheckFn]:
    def decorator(fn: CheckFn) -> CheckFn:
        _REGISTRY.append((check_id, title, category, fn))
        return fn
    return decorator


def available_checks() -> List[tuple]:
    return [(cid, title, cat) for cid, title, cat, _ in _REGISTRY]


def run_checks(ctx: Context, only: Optional[Sequence[str]] = None) -> List[CheckResult]:
    results = []
    for check_id, title, category, fn in _REGISTRY:
        if only and check_id not in only:
            continue
        try:
            res = fn(ctx)
        except Exception as exc:  # a parser bug must never abort the whole report
            res = outcome(Status.ERROR, "Unexpected error while evaluating check: {}: {}".format(
                type(exc).__name__, exc))
        results.append(CheckResult(check_id, title, category, res.status, res.summary,
                                   dict(res.details), res.remediation))
    return results


def command_failure(result: CommandResult) -> Outcome:
    """Map a failed command to SKIP (not applicable / not permitted) or ERROR."""
    if result.not_found:
        return outcome(Status.SKIP, "Required command is unavailable on this system.",
                       command=result.command, error=result.describe_failure())
    if result.needs_privileges:
        return outcome(Status.SKIP, "Insufficient privileges to read this setting.",
                       "Re-run with sudo if this check is required.",
                       command=result.command, error=result.describe_failure())
    return outcome(Status.ERROR, result.describe_failure(), command=result.command)


def parse_key_values(text: str, sep: str = ":") -> Dict[str, str]:
    values = {}
    for line in text.splitlines():
        if sep in line:
            key, _, value = line.partition(sep)
            values[key.strip()] = value.strip()
    return values


# --------------------------------------------------------------------------- system

@check("os_version", "macOS version", "System")
def check_os_version(ctx: Context) -> Outcome:
    res = ctx.runner.run([SW_VERS])
    if not res.ok:
        return command_failure(res)
    info = parse_key_values(res.stdout)
    version = info.get("ProductVersion")
    if not version:
        return outcome(Status.ERROR, "Could not parse sw_vers output.")
    name = info.get("ProductName", "macOS")
    build = info.get("BuildVersion", "unknown")
    return outcome(Status.INFO, "{} {} ({})".format(name, version, build),
                   product_name=name, product_version=version, build_version=build)


@check("hardware", "Hardware", "System")
def check_hardware(ctx: Context) -> Outcome:
    model = ctx.runner.run([SYSCTL, "-n", "hw.model"])
    arch = ctx.runner.run([UNAME, "-m"])
    mem = ctx.runner.run([SYSCTL, "-n", "hw.memsize"])
    if not (model.ok or arch.ok or mem.ok):
        return command_failure(model)
    details: Dict[str, Any] = {}
    parts = []
    if model.ok and model.stdout.strip():
        details["model_identifier"] = model.stdout.strip()
        parts.append(details["model_identifier"])
    if arch.ok and arch.stdout.strip():
        details["architecture"] = arch.stdout.strip()
        parts.append("Apple silicon" if details["architecture"] == "arm64" else "Intel")
    if mem.ok and mem.stdout.strip().isdigit():
        details["memory_gb"] = round(int(mem.stdout.strip()) / 1024 ** 3, 1)
        parts.append("{:g} GB RAM".format(details["memory_gb"]))
    return Outcome(Status.INFO, ", ".join(parts) or "Hardware details unavailable", details)


@check("uptime", "Uptime", "System")
def check_uptime(ctx: Context) -> Outcome:
    res = ctx.runner.run([SYSCTL, "-n", "kern.boottime"])
    if not res.ok:
        return command_failure(res)
    match = re.search(r"sec\s*=\s*(\d+)", res.stdout)
    if not match:
        return outcome(Status.ERROR, "Could not parse kern.boottime output.")
    boot = int(match.group(1))
    days = max(0.0, (ctx.now.timestamp() - boot) / 86400)
    details = {"uptime_days": round(days, 1), "boot_epoch": boot}
    summary = "Up {:.1f} days".format(days)
    if days >= THRESHOLDS["uptime_fail_days"]:
        return Outcome(Status.FAIL, summary, details,
                       "Restart the Mac to apply pending updates and clear long-running state.")
    if days >= THRESHOLDS["uptime_warn_days"]:
        return Outcome(Status.WARN, summary, details, "Schedule a restart soon.")
    return Outcome(Status.PASS, summary, details)


# --------------------------------------------------------------------------- resources

@check("disk_space", "Boot volume free space", "Resources")
def check_disk_space(ctx: Context) -> Outcome:
    res = ctx.runner.run([DF, "-Pk", "/"])
    if not res.ok:
        return command_failure(res)
    lines = [line for line in res.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return outcome(Status.ERROR, "Unexpected df output.")
    fields = lines[-1].split()
    try:
        total_kb, avail_kb = int(fields[1]), int(fields[3])
    except (IndexError, ValueError):
        return outcome(Status.ERROR, "Could not parse df output.")
    if total_kb <= 0:
        return outcome(Status.ERROR, "df reported a zero-size volume.")
    free_gb = avail_kb / 1024 ** 2
    free_pct = avail_kb * 100.0 / total_kb
    details = {"free_gb": round(free_gb, 1), "total_gb": round(total_kb / 1024 ** 2, 1),
               "free_percent": round(free_pct, 1)}
    summary = "{:.1f} GB free ({:.0f}% of {:.0f} GB)".format(free_gb, free_pct, details["total_gb"])
    fix = "Free disk space: empty Trash, remove large unused files, or offload data."
    if free_gb < THRESHOLDS["disk_fail_free_gb"] or free_pct < THRESHOLDS["disk_fail_free_pct"]:
        return Outcome(Status.FAIL, summary, details, fix)
    if free_gb < THRESHOLDS["disk_warn_free_gb"] or free_pct < THRESHOLDS["disk_warn_free_pct"]:
        return Outcome(Status.WARN, summary, details, fix)
    return Outcome(Status.PASS, summary, details)


@check("memory_pressure", "Memory availability", "Resources")
def check_memory(ctx: Context) -> Outcome:
    res = ctx.runner.run([MEMORY_PRESSURE, "-Q"])
    if not res.ok:
        return command_failure(res)
    match = re.search(r"free percentage:\s*(\d+)%", res.stdout)
    if not match:
        return outcome(Status.ERROR, "Could not parse memory_pressure output.")
    pct = int(match.group(1))
    details = {"free_percent": pct}
    summary = "{}% of memory free".format(pct)
    fix = "Quit memory-heavy applications or investigate runaway processes in Activity Monitor."
    if pct < THRESHOLDS["memory_fail_free_pct"]:
        return Outcome(Status.FAIL, summary, details, fix)
    if pct < THRESHOLDS["memory_warn_free_pct"]:
        return Outcome(Status.WARN, summary, details, fix)
    return Outcome(Status.PASS, summary, details)


@check("cpu_load", "CPU load", "Resources")
def check_cpu_load(ctx: Context) -> Outcome:
    load = ctx.runner.run([SYSCTL, "-n", "vm.loadavg"])
    if not load.ok:
        return command_failure(load)
    cores_res = ctx.runner.run([SYSCTL, "-n", "hw.logicalcpu"])
    numbers = re.findall(r"\d+(?:\.\d+)?", load.stdout)
    if len(numbers) < 3:
        return outcome(Status.ERROR, "Could not parse vm.loadavg output.")
    one, five, fifteen = (float(n) for n in numbers[:3])
    cores = int(cores_res.stdout.strip()) if cores_res.ok and cores_res.stdout.strip().isdigit() else 0
    details: Dict[str, Any] = {"load_1m": one, "load_5m": five, "load_15m": fifteen}
    if cores <= 0:
        return Outcome(Status.INFO, "Load average {:.2f} (core count unavailable)".format(five), details)
    per_core = five / cores
    details.update(logical_cpus=cores, load_per_core_5m=round(per_core, 2))
    summary = "5-minute load {:.2f} across {} logical CPUs ({:.2f} per core)".format(five, cores, per_core)
    fix = "Identify sustained high-CPU processes in Activity Monitor."
    if per_core >= THRESHOLDS["load_fail_per_core"]:
        return Outcome(Status.FAIL, summary, details, fix)
    if per_core >= THRESHOLDS["load_warn_per_core"]:
        return Outcome(Status.WARN, summary, details, fix)
    return Outcome(Status.PASS, summary, details)


# --------------------------------------------------------------------------- hardware health

_BATTERY_GOOD = {"good", "normal"}
_BATTERY_WARN = {"fair", "service recommended", "replace soon", "check battery"}
_BATTERY_BAD = {"poor", "replace now", "service battery"}


@check("battery", "Battery health", "Hardware health")
def check_battery(ctx: Context) -> Outcome:
    res = ctx.runner.run([SYSTEM_PROFILER, "SPPowerDataType", "-json"], timeout=90)
    if not res.ok:
        return command_failure(res)
    try:
        items = json.loads(res.stdout).get("SPPowerDataType", [])
    except (ValueError, AttributeError):
        return outcome(Status.ERROR, "Could not parse system_profiler JSON output.")
    info = None
    for item in items:
        if isinstance(item, dict) and isinstance(item.get("sppower_battery_health_info"), dict):
            info = item["sppower_battery_health_info"]
            break
    if info is None:
        return outcome(Status.SKIP, "No internal battery reported (desktop Mac).")
    health = str(info.get("sppower_battery_health", "unknown"))
    cycles = info.get("sppower_battery_cycle_count")
    capacity_raw = info.get("sppower_battery_health_maximum_capacity")
    capacity = None
    if capacity_raw is not None:
        digits = re.search(r"\d+", str(capacity_raw))
        capacity = int(digits.group(0)) if digits else None
    details = {"condition": health, "cycle_count": cycles, "maximum_capacity_percent": capacity}
    summary = "Condition {}".format(health)
    if cycles is not None:
        summary += ", {} cycles".format(cycles)
    if capacity is not None:
        summary += ", {}% maximum capacity".format(capacity)
    fix = "Battery service may be needed; check System Settings > Battery > Battery Health."
    key = health.lower()
    if key in _BATTERY_BAD:
        return Outcome(Status.FAIL, summary, details, fix)
    if key in _BATTERY_WARN or (capacity is not None and capacity < THRESHOLDS["battery_warn_capacity_pct"]):
        return Outcome(Status.WARN, summary, details, fix)
    if key in _BATTERY_GOOD:
        return Outcome(Status.PASS, summary, details)
    return Outcome(Status.INFO, summary, details)


@check("storage_smart", "Boot disk SMART status", "Hardware health")
def check_smart(ctx: Context) -> Outcome:
    res = ctx.runner.run([DISKUTIL, "info", "-plist", "/"])
    if not res.ok:
        return command_failure(res)
    try:
        info = plistlib.loads(res.stdout.encode("utf-8"))
    except Exception:
        return outcome(Status.ERROR, "Could not parse diskutil plist output.")
    smart = str(info.get("SMARTStatus", "Unknown"))
    details = {"smart_status": smart, "solid_state": info.get("SolidState"),
               "file_system": info.get("FilesystemName") or info.get("FilesystemType")}
    if smart == "Verified":
        return Outcome(Status.PASS, "SMART status Verified", details)
    if smart == "Failing":
        return Outcome(Status.FAIL, "SMART status Failing", details,
                       "Back up immediately and arrange storage replacement.")
    return Outcome(Status.INFO, "SMART status {}".format(smart), details)


@check("kernel_panics", "Recent kernel panics", "Hardware health")
def check_panics(ctx: Context) -> Outcome:
    days = THRESHOLDS["panic_lookback_days"]
    res = ctx.runner.run([FIND, DIAGNOSTIC_REPORTS, "-maxdepth", "1", "-type", "f",
                          "-name", "*.panic", "-mtime", "-{}".format(days)])
    if not res.ok:
        if "no such file" in (res.stderr + (res.error or "")).lower():
            return outcome(Status.PASS, "No kernel panic reports found.", panic_count=0, lookback_days=days)
        return command_failure(res)
    files = [line.strip() for line in res.stdout.splitlines() if line.strip()]
    count = len(files)
    details = {"panic_count": count, "lookback_days": days,
               "files": [f.rsplit("/", 1)[-1] for f in files][:20]}
    summary = "{} kernel panic report(s) in the last {} days".format(count, days)
    fix = "Review panic reports in Console > Crash Reports for a common cause (kexts, hardware, OS bug)."
    if count >= THRESHOLDS["panic_fail_count"]:
        return Outcome(Status.FAIL, summary, details, fix)
    if count:
        return Outcome(Status.WARN, summary, details, fix)
    return Outcome(Status.PASS, summary, details)


# --------------------------------------------------------------------------- management

@check("mdm_enrollment", "MDM enrollment", "Management")
def check_mdm(ctx: Context) -> Outcome:
    res = ctx.runner.run([PROFILES, "status", "-type", "enrollment"])
    if not res.ok:
        return command_failure(res)
    info = parse_key_values(res.stdout)
    dep = info.get("Enrolled via DEP", "unknown")
    mdm = info.get("MDM enrollment", "")
    # The MDM server URL is deliberately not recorded: it identifies an organization.
    details = {"enrolled_via_automated_device_enrollment": dep, "mdm_enrollment": mdm or "unknown"}
    lowered = mdm.lower()
    if lowered.startswith("yes") and "user approved" in lowered:
        return Outcome(Status.PASS, "Enrolled in MDM (user approved)", details)
    if lowered.startswith("yes"):
        return Outcome(Status.WARN, "Enrolled in MDM but not user approved", details,
                       "Approve the MDM profile in System Settings so privileged MDM payloads apply.")
    if lowered.startswith("no"):
        return Outcome(Status.INFO, "Not enrolled in MDM", details)
    return Outcome(Status.ERROR, "Could not parse profiles enrollment output.", details)
