# Real-Mac validation checklist

The automated tests run against **synthetic** command output. Before you rely on this tool, run it
on physical hardware and confirm each item below. Record the results in a PR or issue, and add
anonymized real output as new test fixtures. Remove serials, hostnames, usernames, and URLs first.

Suggested coverage: one Apple silicon laptop, one Intel Mac if you still support Intel, one desktop
(no battery), and the oldest and newest macOS versions you support.

```bash
python3 -m mac_health_inspector --json /tmp/health.json --html /tmp/health.html
open /tmp/health.html
```

| Check | Confirm on a real Mac |
|-------|-----------------------|
| `os_version` | Version and build match  > About This Mac. |
| `hardware` | Model identifier matches `sysctl hw.model`. RAM matches About This Mac. |
| `uptime` | Days match `uptime`. |
| `disk_space` | Free GB is close to Finder / Disk Utility "Available" (APFS purgeable space may differ). |
| `memory_pressure` | `memory_pressure -Q` prints a "free percentage" line on your macOS version. |
| `cpu_load` | Values match `sysctl vm.loadavg`. |
| `battery` | Condition, cycle count, and max capacity match System Settings > Battery > Battery Health. The `system_profiler` JSON keys exist on your macOS version. Desktops report `skip`. |
| `storage_smart` | Matches `diskutil info / \| grep SMART`. Expect `Verified` on internal SSDs. |
| `kernel_panics` | Matches `ls /Library/Logs/DiagnosticReports/*.panic`. |
| `mdm_enrollment` | Matches `profiles status -type enrollment`, with no `sudo` needed. The MDM server URL does **not** appear in the report. |
| General | Run as a standard (non-admin) user. No check should crash, and the HTML report should look right in light and dark mode. |
