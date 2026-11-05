"""Test helpers: build fake runners from synthetic command output."""

from __future__ import annotations

import os
from datetime import datetime, timezone

from mac_health_inspector.checks import Context, run_checks
from mac_health_inspector.runner import ReplayRunner

NOW = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXTURES = os.path.join(ROOT, "examples", "fixtures")


def fake_runner(mapping):
    """mapping: {"cmd string": (returncode, stdout[, stderr])}"""
    commands = {}
    for cmd, value in mapping.items():
        rc, out = value[0], value[1]
        err = value[2] if len(value) > 2 else ""
        commands[cmd] = {"returncode": rc, "stdout": out, "stderr": err}
    return ReplayRunner(commands)


def run_one(check_id, mapping, now=NOW):
    results = run_checks(Context(runner=fake_runner(mapping), now=now), only=[check_id])
    assert len(results) == 1, results
    return results[0]
