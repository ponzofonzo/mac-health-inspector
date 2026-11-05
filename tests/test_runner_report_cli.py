"""Tests for the command runner, report rendering, and CLI (no macOS required)."""

import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

from mac_health_inspector import cli
from mac_health_inspector.models import CheckResult, Status
from mac_health_inspector.report import build_report, to_html, to_json, to_text
from mac_health_inspector.runner import CommandRunner, ReplayRunner

from tests.helpers import FIXTURES, NOW


class CommandRunnerTests(unittest.TestCase):
    """These execute real subprocesses (the Python interpreter) on any OS."""

    def test_success(self):
        r = CommandRunner().run([sys.executable, "-c", "print('hello')"])
        self.assertTrue(r.ok)
        self.assertEqual(r.stdout.strip(), "hello")

    def test_nonzero_exit_captured(self):
        r = CommandRunner().run([sys.executable, "-c", "import sys; sys.stderr.write('bad'); sys.exit(3)"])
        self.assertFalse(r.ok)
        self.assertEqual(r.returncode, 3)
        self.assertIn("bad", r.describe_failure())

    def test_missing_binary_does_not_raise(self):
        r = CommandRunner().run(["/nonexistent/definitely-not-a-command"])
        self.assertTrue(r.not_found)
        self.assertFalse(r.ok)

    def test_timeout_does_not_raise(self):
        r = CommandRunner(timeout=0.5).run([sys.executable, "-c", "import time; time.sleep(5)"])
        self.assertTrue(r.timed_out)
        self.assertIn("timed out", r.describe_failure())


class ReplayRunnerTests(unittest.TestCase):
    def test_unknown_command_is_not_found(self):
        self.assertTrue(ReplayRunner({}).run(["/usr/bin/true"]).not_found)

    def test_loads_fixture_and_now(self):
        runner = ReplayRunner.from_file(os.path.join(FIXTURES, "healthy-laptop.json"))
        self.assertEqual(runner.now, NOW)
        self.assertTrue(runner.run(["/usr/bin/sw_vers"]).ok)


def sample_results():
    return [
        CheckResult("a", "Alpha", "Cat 1", Status.PASS, "fine"),
        CheckResult("b", "Beta <b>", "Cat 1", Status.WARN, "<script>alert(1)</script>",
                    {"k": "<img src=x>", "nested": {"x": 1}}, "Do & fix"),
        CheckResult("c", "Gamma", "Cat 2", Status.SKIP, "n/a"),
    ]


class ReportTests(unittest.TestCase):
    def report(self, source="live"):
        return build_report(sample_results(), tool="t", version="1", generated_at=NOW, data_source=source)

    def test_summary_counts_and_overall(self):
        rep = self.report()
        self.assertEqual(rep["summary"]["overall_status"], Status.WARN)
        self.assertEqual(rep["summary"]["counts"]["pass"], 1)
        self.assertEqual(rep["summary"]["counts"]["skip"], 1)
        self.assertEqual(rep["summary"]["total"], 3)

    def test_json_round_trip(self):
        rep = json.loads(to_json(self.report()))
        self.assertEqual(rep["schema_version"], "1.0")
        self.assertEqual(len(rep["checks"]), 3)
        self.assertEqual(rep["generated_at"], "2026-01-15T12:00:00+00:00")

    def test_html_escapes_untrusted_text(self):
        page = to_html(self.report(), "Title")
        self.assertNotIn("<script>alert", page)
        self.assertNotIn("<img src=x>", page)
        self.assertIn("&lt;script&gt;", page)
        self.assertIn("Do &amp; fix", page)
        self.assertTrue(page.startswith("<!doctype html>"))

    def test_html_synthetic_banner_only_for_replay(self):
        self.assertNotIn("Synthetic data", to_html(self.report(), "T"))
        self.assertIn("Synthetic data", to_html(self.report("replay:x.json"), "T"))

    def test_text(self):
        text = to_text(self.report())
        self.assertIn("[WARN ]", text)
        self.assertIn("Overall: WARN", text)


class CliTests(unittest.TestCase):
    def run_cli(self, *argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = cli.main(list(argv))
        return code, out.getvalue(), err.getvalue()

    def test_healthy_replay_writes_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            j, h = os.path.join(tmp, "out", "r.json"), os.path.join(tmp, "out", "r.html")
            code, out, _ = self.run_cli("--replay", os.path.join(FIXTURES, "healthy-laptop.json"),
                                        "--json", j, "--html", h, "--fail-on", "warn")
            self.assertEqual(code, 0, out)
            with open(j, encoding="utf-8") as fh:
                rep = json.load(fh)
            with open(h, encoding="utf-8") as fh:
                page = fh.read()
            self.assertEqual(rep["summary"]["overall_status"], Status.PASS)
            self.assertEqual(rep["data_source"], "replay:healthy-laptop.json")
            self.assertIn("Mac Health Report", page)

    def test_degraded_replay_exit_codes(self):
        fixture = os.path.join(FIXTURES, "degraded-laptop.json")
        self.assertEqual(self.run_cli("--replay", fixture, "--quiet")[0], 0)
        self.assertEqual(self.run_cli("--replay", fixture, "--quiet", "--fail-on", "fail")[0], 1)
        code, out, _ = self.run_cli("--replay", fixture, "--json", "-")
        rep = json.loads(out)
        statuses = {c["id"]: c["status"] for c in rep["checks"]}
        self.assertEqual(statuses["memory_pressure"], Status.FAIL)
        self.assertEqual(statuses["storage_smart"], Status.ERROR)
        self.assertEqual(statuses["mdm_enrollment"], Status.WARN)

    def test_check_filter(self):
        code, out, _ = self.run_cli("--replay", os.path.join(FIXTURES, "healthy-laptop.json"),
                                    "--check", "uptime", "--json", "-")
        self.assertEqual([c["id"] for c in json.loads(out)["checks"]], ["uptime"])

    def test_unknown_check(self):
        self.assertEqual(self.run_cli("--check", "nope")[0], 3)

    def test_list_checks(self):
        code, out, _ = self.run_cli("--list-checks")
        self.assertEqual(code, 0)
        self.assertIn("disk_space", out)

    def test_refuses_live_run_off_macos(self):
        with mock.patch.object(cli.sys, "platform", "linux"):
            code, _, err = self.run_cli()
        self.assertEqual(code, 3)
        self.assertIn("macOS only", err)

    def test_bad_fixture(self):
        self.assertEqual(self.run_cli("--replay", "/nonexistent/fixture.json")[0], 3)


if __name__ == "__main__":
    unittest.main()
