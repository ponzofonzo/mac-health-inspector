"""Read-only command execution that never raises.

Every macOS command goes through a runner so that:
  * failures (missing binary, timeout, non-zero exit, permission problems)
    become data instead of exceptions, and
  * tests and demos can substitute recorded or synthetic output.
"""

from __future__ import annotations

import json
import shlex
import subprocess
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, Optional, Sequence, Tuple

DEFAULT_TIMEOUT = 30

RC_NOT_FOUND = 127
RC_TIMEOUT = 124
RC_NOT_EXECUTABLE = 126


@dataclass(frozen=True)
class CommandResult:
    args: Tuple[str, ...]
    returncode: int
    stdout: str = ""
    stderr: str = ""
    error: Optional[str] = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.returncode == 0

    @property
    def command(self) -> str:
        return shlex.join(self.args)

    @property
    def timed_out(self) -> bool:
        return self.returncode == RC_TIMEOUT and self.error is not None

    @property
    def not_found(self) -> bool:
        return self.returncode == RC_NOT_FOUND

    @property
    def needs_privileges(self) -> bool:
        text = (self.stderr + " " + self.stdout).lower()
        markers = ("requires root", "must be run as root", "run as root", "permission denied",
                   "operation not permitted", "not permitted", "requires administrator",
                   "must be root", "need administrator")
        return self.returncode != 0 and any(m in text for m in markers)

    def describe_failure(self) -> str:
        if self.error:
            return self.error
        detail = (self.stderr.strip() or self.stdout.strip()).splitlines()
        first = detail[0] if detail else "no output"
        return "`{}` exited with status {}: {}".format(self.command, self.returncode, first)


class CommandRunner:
    """Runs real commands with a timeout. Never uses a shell."""

    def __init__(self, timeout: float = DEFAULT_TIMEOUT) -> None:
        self.timeout = timeout

    def run(self, args: Sequence[str], timeout: Optional[float] = None) -> CommandResult:
        argv = tuple(args)
        limit = self.timeout if timeout is None else timeout
        try:
            proc = subprocess.run(
                list(argv),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=limit,
                check=False,
            )
        except FileNotFoundError:
            return CommandResult(argv, RC_NOT_FOUND, error="command not found: {}".format(argv[0]))
        except PermissionError:
            return CommandResult(argv, RC_NOT_EXECUTABLE, error="permission denied: {}".format(argv[0]))
        except subprocess.TimeoutExpired:
            return CommandResult(argv, RC_TIMEOUT, error="`{}` timed out after {}s".format(shlex.join(argv), limit))
        except OSError as exc:
            return CommandResult(argv, 1, error="could not run {}: {}".format(argv[0], exc))
        return CommandResult(argv, proc.returncode, proc.stdout or "", proc.stderr or "")


class ReplayRunner:
    """Returns pre-recorded output from a fixture file instead of running anything.

    Fixture format::

        {
          "description": "Synthetic ...",
          "now": "2026-01-15T12:00:00Z",
          "commands": {
            "/usr/bin/sw_vers": {"returncode": 0, "stdout": "...", "stderr": ""}
          }
        }

    Commands absent from the fixture behave like a missing binary.
    """

    def __init__(self, commands: Dict[str, dict], description: str = "", now: Optional[datetime] = None) -> None:
        self.commands = commands
        self.description = description
        self.now = now
        self.calls = []

    @classmethod
    def from_file(cls, path: str) -> "ReplayRunner":
        with open(path, "r", encoding="utf-8") as handle:
            data = json.load(handle)
        now = None
        if data.get("now"):
            now = datetime.fromisoformat(data["now"].replace("Z", "+00:00"))
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
        return cls(data.get("commands", {}), data.get("description", ""), now)

    def run(self, args: Sequence[str], timeout: Optional[float] = None) -> CommandResult:
        argv = tuple(args)
        key = shlex.join(argv)
        self.calls.append(key)
        entry = self.commands.get(key)
        if entry is None:
            return CommandResult(argv, RC_NOT_FOUND, error="not recorded in replay fixture: {}".format(key))
        return CommandResult(
            argv,
            int(entry.get("returncode", 0)),
            entry.get("stdout", ""),
            entry.get("stderr", ""),
            entry.get("error"),
        )
