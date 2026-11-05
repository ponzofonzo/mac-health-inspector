# Mac Health Inspector

[![CI](https://github.com/ponzofonzo/mac-health-inspector/actions/workflows/ci.yml/badge.svg)](https://github.com/ponzofonzo/mac-health-inspector/actions/workflows/ci.yml)
![Python 3.9+](https://img.shields.io/badge/python-3.9%2B-blue)
![Dependencies: none](https://img.shields.io/badge/dependencies-none-brightgreen)
![License: MIT](https://img.shields.io/badge/license-MIT-lightgrey)

Read-only macOS health diagnostics for help desk triage and fleet spot checks. Runs ten checks
with built-in macOS tools and writes a console summary, a JSON report for automation, and a
self-contained HTML report for people.

- **Read-only.** Nothing is changed, installed, or sent over the network.
- **No dependencies.** Python 3.9+ standard library only.
- **Fails gracefully.** A missing tool, a timeout, a permission problem, or unexpected output turns
  into a `skip` or `error` result for that one check. The report still gets written.
- **Privacy-conscious.** Reports leave out serial numbers, hardware UUIDs, hostnames, usernames,
  and MDM server URLs.

## Checks

| ID | Category | What it looks at | Pass / Warn / Fail |
|----|----------|------------------|--------------------|
| `os_version` | System | `sw_vers` | info only |
| `hardware` | System | model identifier, architecture, RAM (`sysctl`, `uname`) | info only |
| `uptime` | System | `sysctl kern.boottime` | < 14 days / ≥ 14 / ≥ 30 |
| `disk_space` | Resources | `df -Pk /` | warn < 25 GB or < 15 %; fail < 10 GB or < 5 % |
| `memory_pressure` | Resources | `memory_pressure -Q` free % | warn < 25 %; fail < 10 % |
| `cpu_load` | Resources | 5-minute load ÷ logical CPUs | warn ≥ 1.0; fail ≥ 2.0 |
| `battery` | Hardware health | `system_profiler SPPowerDataType -json` | condition + max capacity (< 80 % warns); skipped on desktops |
| `storage_smart` | Hardware health | `diskutil info -plist /` SMART status | Verified / — / Failing |
| `kernel_panics` | Hardware health | `*.panic` files in `/Library/Logs/DiagnosticReports`, last 7 days | 0 / 1–2 / ≥ 3 |
| `mdm_enrollment` | Management | `profiles status -type enrollment` | user approved / not user approved / (not enrolled = info) |

Thresholds live in one `THRESHOLDS` dictionary at the top of
[`mac_health_inspector/checks.py`](mac_health_inspector/checks.py).

Each result has one of six statuses: `pass`, `warn`, `fail`, `error` (the check could not be
evaluated), `info` (facts only), or `skip` (not applicable, command missing, or more privileges
needed).

## Requirements

- macOS with a `python3` 3.9 or newer. Apple's `/usr/bin/python3` needs the Command Line Tools
  installed. Managed fleets usually deploy their own Python framework.
- Standard user rights are enough for every check. None of them need `sudo`.

## Usage

```bash
git clone https://github.com/ponzofonzo/mac-health-inspector.git
cd mac-health-inspector

# Console summary only
python3 -m mac_health_inspector

# JSON + HTML reports
python3 -m mac_health_inspector --json reports/health.json --html reports/health.html

# JSON to stdout (for piping into jq or another tool)
python3 -m mac_health_inspector --json - | jq '.summary'

# Run selected checks; exit 1 if anything is warn or worse
python3 -m mac_health_inspector --check disk_space --check battery --fail-on warn

python3 -m mac_health_inspector --list-checks
```

| Option | Meaning |
|--------|---------|
| `--json PATH` | Write the JSON report (`-` = stdout). |
| `--html PATH` | Write the HTML report. |
| `--check ID` | Run only this check. You can repeat it. |
| `--fail-on never\|warn\|fail` | Exit code policy. The default, `never`, always exits 0 after a successful run. |
| `--timeout SECONDS` | Timeout for each command (default 30; `system_profiler` gets 90). |
| `--replay FIXTURE` | Render from recorded or synthetic output instead of running commands. |
| `--quiet` | Don't print the console summary. |

**Exit codes:** `0` success · `1` the `--fail-on` threshold was hit · `2` invalid arguments ·
`3` fatal (not macOS, unreadable fixture, can't write the report).

### Running from Jamf Pro (optional)

You can run the tool as a policy script on Macs that already have a Python 3.9+ interpreter.
Copy the `mac_health_inspector` package to a known path, for example with a package, then run:

```bash
#!/bin/bash
/usr/local/bin/python3 -m mac_health_inspector \
  --json "/Library/Application Support/MacHealth/health.json" --quiet
exit 0
```

## Examples

The files in [`examples/`](examples/) are rendered **from synthetic fixtures**, not from a real Mac:

- [`examples/healthy-report.json`](examples/healthy-report.json) / [`.html`](examples/healthy-report.html)
- [`examples/degraded-report.json`](examples/degraded-report.json) / [`.html`](examples/degraded-report.html)
  shows warnings, a failure, and a failed command that is reported as `error`.

You can rebuild them on any OS:

```bash
python3 -m mac_health_inspector --replay examples/fixtures/degraded-laptop.json \
  --json examples/degraded-report.json --html examples/degraded-report.html
```

The HTML report and the console summary put a "synthetic data" banner on every replayed report.

## JSON report format

```json
{
  "schema_version": "1.0",
  "tool": {"name": "mac-health-inspector", "version": "1.0.0"},
  "generated_at": "2026-01-15T12:00:00+00:00",
  "data_source": "live",
  "summary": {"overall_status": "warn", "total": 10,
              "counts": {"pass": 7, "warn": 1, "fail": 0, "error": 0, "info": 2, "skip": 0}},
  "checks": [
    {"id": "disk_space", "title": "Boot volume free space", "category": "Resources",
     "status": "warn", "summary": "50.0 GB free (11% of 460 GB)",
     "details": {"free_gb": 50.0, "total_gb": 460.4, "free_percent": 10.9},
     "remediation": "Free disk space: ..."}
  ]
}
```

`overall_status` is the worst status among `pass`/`warn`/`error`/`fail`. `info` and `skip` don't
affect it.

## Testing

| Layer | Where it runs | What it proves |
|-------|---------------|----------------|
| Unit tests with mocked command output | Any OS (`python3 -m unittest discover -s tests -t .`) | Parsing, thresholds, failure handling, HTML escaping, CLI behavior |
| Replay fixtures | Any OS | End-to-end report generation from realistic synthetic output |
| CI live smoke run | GitHub-hosted `macos-latest` VM | The tool runs against a real macOS install and produces a valid report |
| **Manual validation** | **Physical Macs** | That parsers match real output across macOS versions, Intel and Apple silicon, laptops and desktops |

The unit tests use hand-written synthetic output that follows the documented or commonly observed
format of each command. A virtual machine has no battery, SMART data, or MDM enrollment. Those
checks are only verified once someone runs the tool on physical hardware. See
[`docs/VALIDATION.md`](docs/VALIDATION.md) for the manual checklist.

## Project layout

```
mac_health_inspector/
  checks.py    # the ten checks and the THRESHOLDS table
  runner.py    # subprocess wrapper (never raises) + replay runner
  report.py    # JSON / HTML / text rendering
  cli.py       # argument parsing and exit codes
examples/      # synthetic fixtures and the reports rendered from them
tests/         # unittest suite, no third-party packages
```

## About this project

I've built tooling like this for employers during my career; that code belongs to them. This
repository is a generic version written independently, from scratch, for public use. It contains
no employer source code, configurations, credentials, device data, or internal information, and it
relies only on built-in macOS commands and publicly documented behavior.

## License

MIT. See [LICENSE](LICENSE).
