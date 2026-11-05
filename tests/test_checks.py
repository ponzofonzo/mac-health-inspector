"""Unit tests for individual checks using synthetic command output (no macOS required)."""

import json
import unittest

from mac_health_inspector.checks import Context, run_checks
from mac_health_inspector.models import Status

from tests.helpers import NOW, fake_runner, run_one

BOOT = int(NOW.timestamp())
DF_HEADER = "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
GB = 1024 ** 2  # KiB per GiB


def df(total_gb, avail_gb):
    return DF_HEADER + "/dev/disk3s1s1 {} 100 {} 5% /\n".format(int(total_gb * GB), int(avail_gb * GB))


def power_json(health=None, cycles=None, capacity=None):
    if health is None:
        return json.dumps({"SPPowerDataType": [{"_name": "sppower_ac_charger_information"}]})
    info = {"sppower_battery_health": health}
    if cycles is not None:
        info["sppower_battery_cycle_count"] = cycles
    if capacity is not None:
        info["sppower_battery_health_maximum_capacity"] = capacity
    return json.dumps({"SPPowerDataType": [{"sppower_battery_health_info": info}]})


def plist(smart):
    return ('<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
            "<key>SMARTStatus</key><string>{}</string></dict></plist>".format(smart))


PANIC_CMD = ("/usr/bin/find /Library/Logs/DiagnosticReports -maxdepth 1 -type f "
             "-name '*.panic' -mtime -7")


class OsVersionTests(unittest.TestCase):
    def test_parses_sw_vers(self):
        r = run_one("os_version", {"/usr/bin/sw_vers": (0, "ProductName:\tmacOS\nProductVersion:\t15.2\n"
                                                           "BuildVersion:\t24C101\n")})
        self.assertEqual(r.status, Status.INFO)
        self.assertEqual(r.details["product_version"], "15.2")
        self.assertIn("24C101", r.summary)

    def test_missing_command_is_skip(self):
        self.assertEqual(run_one("os_version", {}).status, Status.SKIP)

    def test_unparseable_output_is_error(self):
        self.assertEqual(run_one("os_version", {"/usr/bin/sw_vers": (0, "garbage")}).status, Status.ERROR)

    def test_nonzero_exit_is_error(self):
        r = run_one("os_version", {"/usr/bin/sw_vers": (1, "", "boom")})
        self.assertEqual(r.status, Status.ERROR)
        self.assertIn("boom", r.summary)


class HardwareTests(unittest.TestCase):
    def test_summary(self):
        r = run_one("hardware", {"/usr/sbin/sysctl -n hw.model": (0, "Mac15,6\n"),
                                 "/usr/bin/uname -m": (0, "arm64\n"),
                                 "/usr/sbin/sysctl -n hw.memsize": (0, "17179869184\n")})
        self.assertEqual(r.status, Status.INFO)
        self.assertEqual(r.details["memory_gb"], 16.0)
        self.assertIn("Apple silicon", r.summary)

    def test_partial_data_still_reports(self):
        r = run_one("hardware", {"/usr/bin/uname -m": (0, "x86_64\n")})
        self.assertEqual(r.status, Status.INFO)
        self.assertIn("Intel", r.summary)

    def test_all_failed_is_skip(self):
        self.assertEqual(run_one("hardware", {}).status, Status.SKIP)


class UptimeTests(unittest.TestCase):
    def boot(self, days):
        return {"/usr/sbin/sysctl -n kern.boottime":
                (0, "{{ sec = {}, usec = 0 }} Mon Jan 12 12:00:00 2026\n".format(BOOT - int(days * 86400)))}

    def test_thresholds(self):
        self.assertEqual(run_one("uptime", self.boot(2)).status, Status.PASS)
        self.assertEqual(run_one("uptime", self.boot(15)).status, Status.WARN)
        self.assertEqual(run_one("uptime", self.boot(31)).status, Status.FAIL)

    def test_reports_days(self):
        self.assertEqual(run_one("uptime", self.boot(3)).details["uptime_days"], 3.0)

    def test_bad_output(self):
        self.assertEqual(run_one("uptime", {"/usr/sbin/sysctl -n kern.boottime": (0, "nope")}).status,
                         Status.ERROR)


class DiskTests(unittest.TestCase):
    def test_pass(self):
        r = run_one("disk_space", {"/bin/df -Pk /": (0, df(500, 200))})
        self.assertEqual(r.status, Status.PASS)
        self.assertEqual(r.details["free_gb"], 200.0)

    def test_warn_by_percent(self):
        self.assertEqual(run_one("disk_space", {"/bin/df -Pk /": (0, df(500, 60))}).status, Status.WARN)

    def test_fail_by_absolute(self):
        self.assertEqual(run_one("disk_space", {"/bin/df -Pk /": (0, df(100, 8))}).status, Status.FAIL)

    def test_garbage(self):
        self.assertEqual(run_one("disk_space", {"/bin/df -Pk /": (0, "x\ny z\n")}).status, Status.ERROR)
        self.assertEqual(run_one("disk_space", {"/bin/df -Pk /": (0, "")}).status, Status.ERROR)


class MemoryTests(unittest.TestCase):
    def mp(self, pct):
        return {"/usr/bin/memory_pressure -Q": (0, "System-wide memory free percentage: {}%\n".format(pct))}

    def test_thresholds(self):
        self.assertEqual(run_one("memory_pressure", self.mp(60)).status, Status.PASS)
        self.assertEqual(run_one("memory_pressure", self.mp(20)).status, Status.WARN)
        self.assertEqual(run_one("memory_pressure", self.mp(5)).status, Status.FAIL)

    def test_unparseable(self):
        r = run_one("memory_pressure", {"/usr/bin/memory_pressure -Q": (0, "unexpected")})
        self.assertEqual(r.status, Status.ERROR)


class CpuLoadTests(unittest.TestCase):
    def load(self, five, cores="8\n"):
        m = {"/usr/sbin/sysctl -n vm.loadavg": (0, "{{ 1.00 {} 1.00 }}\n".format(five))}
        if cores is not None:
            m["/usr/sbin/sysctl -n hw.logicalcpu"] = (0, cores)
        return m

    def test_thresholds(self):
        self.assertEqual(run_one("cpu_load", self.load("2.00")).status, Status.PASS)
        self.assertEqual(run_one("cpu_load", self.load("9.00")).status, Status.WARN)
        self.assertEqual(run_one("cpu_load", self.load("17.00")).status, Status.FAIL)

    def test_missing_core_count_is_info(self):
        self.assertEqual(run_one("cpu_load", self.load("3.00", cores=None)).status, Status.INFO)


class BatteryTests(unittest.TestCase):
    CMD = "/usr/sbin/system_profiler SPPowerDataType -json"

    def test_desktop_is_skip(self):
        self.assertEqual(run_one("battery", {self.CMD: (0, power_json())}).status, Status.SKIP)

    def test_normal(self):
        r = run_one("battery", {self.CMD: (0, power_json("Normal", 100, "95%"))})
        self.assertEqual(r.status, Status.PASS)
        self.assertEqual(r.details["maximum_capacity_percent"], 95)
        self.assertEqual(r.details["cycle_count"], 100)

    def test_legacy_good_string(self):
        self.assertEqual(run_one("battery", {self.CMD: (0, power_json("Good"))}).status, Status.PASS)

    def test_service_recommended(self):
        self.assertEqual(run_one("battery", {self.CMD: (0, power_json("Service Recommended"))}).status,
                         Status.WARN)

    def test_low_capacity_warns_even_if_normal(self):
        self.assertEqual(run_one("battery", {self.CMD: (0, power_json("Normal", 900, "72%"))}).status,
                         Status.WARN)

    def test_poor(self):
        self.assertEqual(run_one("battery", {self.CMD: (0, power_json("Poor"))}).status, Status.FAIL)

    def test_invalid_json(self):
        self.assertEqual(run_one("battery", {self.CMD: (0, "{not json")}).status, Status.ERROR)

    def test_timeout_is_error(self):
        from mac_health_inspector.runner import CommandResult

        class TimeoutRunner:
            def run(self, args, timeout=None):
                return CommandResult(tuple(args), 124, error="timed out after 90s")

        r = run_checks(Context(runner=TimeoutRunner(), now=NOW), only=["battery"])[0]
        self.assertEqual(r.status, Status.ERROR)
        self.assertIn("timed out", r.summary)


class SmartTests(unittest.TestCase):
    CMD = "/usr/sbin/diskutil info -plist /"

    def test_states(self):
        self.assertEqual(run_one("storage_smart", {self.CMD: (0, plist("Verified"))}).status, Status.PASS)
        self.assertEqual(run_one("storage_smart", {self.CMD: (0, plist("Failing"))}).status, Status.FAIL)
        self.assertEqual(run_one("storage_smart", {self.CMD: (0, plist("Not Supported"))}).status, Status.INFO)

    def test_bad_plist(self):
        self.assertEqual(run_one("storage_smart", {self.CMD: (0, "<plist>")}).status, Status.ERROR)


class PanicTests(unittest.TestCase):
    def test_none(self):
        self.assertEqual(run_one("kernel_panics", {PANIC_CMD: (0, "")}).status, Status.PASS)

    def test_one_is_warn(self):
        r = run_one("kernel_panics", {PANIC_CMD: (0, "/Library/Logs/DiagnosticReports/a.panic\n")})
        self.assertEqual(r.status, Status.WARN)
        self.assertEqual(r.details["files"], ["a.panic"])

    def test_many_is_fail(self):
        out = "".join("/Library/Logs/DiagnosticReports/{}.panic\n".format(i) for i in range(3))
        self.assertEqual(run_one("kernel_panics", {PANIC_CMD: (0, out)}).status, Status.FAIL)

    def test_missing_directory_is_pass(self):
        r = run_one("kernel_panics", {PANIC_CMD: (1, "", "find: /Library/Logs/DiagnosticReports: "
                                                         "No such file or directory")})
        self.assertEqual(r.status, Status.PASS)

    def test_permission_denied_is_skip(self):
        r = run_one("kernel_panics", {PANIC_CMD: (1, "", "find: Permission denied")})
        self.assertEqual(r.status, Status.SKIP)


class MdmTests(unittest.TestCase):
    CMD = "/usr/bin/profiles status -type enrollment"

    def test_user_approved(self):
        r = run_one("mdm_enrollment", {self.CMD: (0, "Enrolled via DEP: Yes\nMDM enrollment: Yes (User Approved)\n"
                                                     "MDM server: https://mdm.example.com/mdm\n")})
        self.assertEqual(r.status, Status.PASS)
        self.assertNotIn("example.com", json.dumps(r.to_dict()), "server URL must not be recorded")

    def test_not_user_approved(self):
        r = run_one("mdm_enrollment", {self.CMD: (0, "Enrolled via DEP: No\nMDM enrollment: Yes\n")})
        self.assertEqual(r.status, Status.WARN)

    def test_not_enrolled(self):
        r = run_one("mdm_enrollment", {self.CMD: (0, "Enrolled via DEP: No\nMDM enrollment: No\n")})
        self.assertEqual(r.status, Status.INFO)

    def test_requires_root_is_skip(self):
        r = run_one("mdm_enrollment", {self.CMD: (1, "", "profiles: this command requires root")})
        self.assertEqual(r.status, Status.SKIP)


class FrameworkTests(unittest.TestCase):
    def test_exception_in_check_becomes_error_not_crash(self):
        class ExplodingRunner:
            def run(self, args, timeout=None):
                raise RuntimeError("synthetic failure")

        results = run_checks(Context(runner=ExplodingRunner(), now=NOW))
        self.assertTrue(results)
        self.assertTrue(all(r.status == Status.ERROR for r in results))
        self.assertIn("synthetic failure", results[0].summary)

    def test_no_commands_available_never_crashes(self):
        results = run_checks(Context(runner=fake_runner({}), now=NOW))
        self.assertEqual(len(results), 10)
        for r in results:
            self.assertIn(r.status, (Status.SKIP, Status.ERROR), r.id)


if __name__ == "__main__":
    unittest.main()
