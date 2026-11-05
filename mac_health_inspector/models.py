"""Result model shared by every check."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, Iterable, NamedTuple


class Status:
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    ERROR = "error"
    INFO = "info"
    SKIP = "skip"
    ALL = (PASS, WARN, FAIL, ERROR, INFO, SKIP)


# Higher number = worse. INFO/SKIP never drive the overall status.
SEVERITY = {
    Status.FAIL: 4,
    Status.ERROR: 3,
    Status.WARN: 2,
    Status.PASS: 1,
    Status.INFO: 0,
    Status.SKIP: 0,
}


class Outcome(NamedTuple):
    status: str
    summary: str
    details: Dict[str, Any] = {}
    remediation: str = ""


def outcome(status: str, summary: str, remediation: str = "", **details: Any) -> Outcome:
    return Outcome(status, summary, details, remediation)


@dataclass
class CheckResult:
    id: str
    title: str
    category: str
    status: str
    summary: str
    details: Dict[str, Any] = field(default_factory=dict)
    remediation: str = ""

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def overall_status(results: Iterable[CheckResult]) -> str:
    worst = Status.INFO
    for result in results:
        if SEVERITY[result.status] > SEVERITY[worst]:
            worst = result.status
    return worst
