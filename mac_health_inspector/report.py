"""JSON and self-contained HTML report rendering."""

from __future__ import annotations

import html
import json
from collections import OrderedDict
from datetime import datetime
from typing import Any, Dict, List, Optional

from .models import CheckResult, Status, overall_status

SCHEMA_VERSION = "1.0"


def build_report(
    results: List[CheckResult],
    *,
    tool: str,
    version: str,
    generated_at: datetime,
    data_source: str,
    summary_extra: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    counts = OrderedDict((status, 0) for status in Status.ALL)
    for result in results:
        counts[result.status] += 1
    summary: Dict[str, Any] = {
        "overall_status": overall_status(results),
        "total": len(results),
        "counts": dict(counts),
    }
    if summary_extra:
        summary.update(summary_extra)
    return {
        "schema_version": SCHEMA_VERSION,
        "tool": {"name": tool, "version": version},
        "generated_at": generated_at.isoformat(timespec="seconds"),
        "data_source": data_source,
        "summary": summary,
        "checks": [r.to_dict() for r in results],
    }


def to_json(report: Dict[str, Any]) -> str:
    return json.dumps(report, indent=2, sort_keys=False) + "\n"


_CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1d1f23;--muted:#5f6673;--border:#e2e5ea;
--pass:#1a7f37;--pass-bg:#dafbe1;--warn:#9a6700;--warn-bg:#fff8c5;--fail:#cf222e;--fail-bg:#ffebe9;
--error:#8250df;--error-bg:#fbefff;--info:#0969da;--info-bg:#ddf4ff;--skip:#57606a;--skip-bg:#eaeef2}
@media (prefers-color-scheme:dark){:root{--bg:#0d1117;--card:#161b22;--text:#e6edf3;--muted:#8d96a0;
--border:#30363d;--pass:#3fb950;--pass-bg:#12261e;--warn:#d29922;--warn-bg:#272115;--fail:#f85149;
--fail-bg:#2d1517;--error:#bc8cff;--error-bg:#231a33;--info:#58a6ff;--info-bg:#121d2f;--skip:#8d96a0;--skip-bg:#1c2128}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--text);font:14px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",Helvetica,Arial,sans-serif}
main{max-width:1100px;margin:0 auto;padding:32px 16px}
h1{margin:0 0 4px;font-size:24px}
.meta{color:var(--muted);font-size:13px}
.banner{margin:16px 0;padding:10px 14px;border-radius:8px;background:var(--warn-bg);color:var(--warn);border:1px solid var(--border)}
.headline{margin:20px 0 0;font-size:18px;font-weight:600}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(110px,1fr));gap:12px;margin:16px 0 8px}
.card{background:var(--card);border:1px solid var(--border);border-radius:10px;padding:12px 14px}
.card .n{font-size:24px;font-weight:600}
.card .l{color:var(--muted);font-size:12px;text-transform:uppercase;letter-spacing:.04em}
section{background:var(--card);border:1px solid var(--border);border-radius:10px;margin:16px 0;overflow:hidden}
section h2{font-size:15px;margin:0;padding:12px 16px;border-bottom:1px solid var(--border)}
table{width:100%;border-collapse:collapse}
td,th{padding:10px 16px;text-align:left;vertical-align:top;border-bottom:1px solid var(--border)}
tr:last-child td{border-bottom:0}
th{font-size:12px;color:var(--muted);font-weight:600;text-transform:uppercase}
td.s{width:90px}td.t{width:28%;font-weight:600}
.pill{display:inline-block;min-width:56px;text-align:center;padding:2px 8px;border-radius:999px;font-size:12px;font-weight:600;text-transform:uppercase}
.pass{color:var(--pass);background:var(--pass-bg)}.warn{color:var(--warn);background:var(--warn-bg)}
.fail{color:var(--fail);background:var(--fail-bg)}.error{color:var(--error);background:var(--error-bg)}
.info{color:var(--info);background:var(--info-bg)}.skip{color:var(--skip);background:var(--skip-bg)}
.rem{color:var(--muted);font-size:13px;margin-top:4px}
details{margin-top:6px}details summary{cursor:pointer;color:var(--muted);font-size:12px}
dl{display:grid;grid-template-columns:max-content 1fr;gap:2px 12px;margin:6px 0 0;font-size:12px}
dt{color:var(--muted)}dd{margin:0;font-family:ui-monospace,SFMono-Regular,Menlo,monospace;word-break:break-word}
footer{color:var(--muted);font-size:12px;margin-top:24px}
@media (max-width:640px){td,th{padding:8px 10px}td.t{width:auto}}
"""


def _e(value: Any) -> str:
    return html.escape(str(value), quote=True)


def _detail_value(value: Any) -> str:
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, sort_keys=True)
    if value is None:
        return "—"
    return str(value)


def _row(check: Dict[str, Any]) -> str:
    status = check["status"] if check["status"] in Status.ALL else Status.INFO
    parts = ['<tr><td class="s"><span class="pill {0}">{0}</span></td>'.format(status)]
    parts.append('<td class="t">{}</td><td>{}'.format(_e(check["title"]), _e(check["summary"])))
    if check.get("remediation"):
        parts.append('<div class="rem">Remediation: {}</div>'.format(_e(check["remediation"])))
    details = check.get("details") or {}
    if details:
        items = "".join(
            "<dt>{}</dt><dd>{}</dd>".format(_e(k), _e(_detail_value(v))) for k, v in details.items()
        )
        parts.append("<details><summary>Details</summary><dl>{}</dl></details>".format(items))
    parts.append("</td></tr>")
    return "".join(parts)


def to_html(report: Dict[str, Any], title: str) -> str:
    summary = report["summary"]
    groups: "OrderedDict[str, List[Dict[str, Any]]]" = OrderedDict()
    for check in report["checks"]:
        groups.setdefault(check["category"], []).append(check)

    out = [
        "<!doctype html>",
        '<html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>{}</title><style>{}</style></head><body><main>".format(_e(title), _CSS),
        "<header><h1>{}</h1>".format(_e(title)),
        '<div class="meta">Generated {} · {} {} · schema {}</div></header>'.format(
            _e(report["generated_at"]), _e(report["tool"]["name"]), _e(report["tool"]["version"]),
            _e(report["schema_version"]),
        ),
    ]
    if report["data_source"] != "live":
        out.append(
            '<div class="banner">Synthetic data: this report was rendered from a replay fixture '
            "({}), not collected from a real Mac.</div>".format(_e(report["data_source"]))
        )
    headline = summary.get("headline")
    if headline:
        out.append('<div class="headline">{}</div>'.format(_e(headline)))
    out.append('<div class="cards">')
    out.append('<div class="card"><div class="n"><span class="pill {0}">{0}</span></div>'
               '<div class="l">Overall</div></div>'.format(_e(summary["overall_status"])))
    for status, count in summary["counts"].items():
        out.append('<div class="card"><div class="n">{}</div><div class="l">{}</div></div>'.format(
            _e(count), _e(status)))
    out.append("</div>")
    for category, checks in groups.items():
        out.append("<section><h2>{}</h2><table>".format(_e(category)))
        out.append("<thead><tr><th>Status</th><th>Check</th><th>Result</th></tr></thead><tbody>")
        out.extend(_row(c) for c in checks)
        out.append("</tbody></table></section>")
    out.append("<footer>Read-only report. No settings were changed while collecting this data.</footer>")
    out.append("</main></body></html>\n")
    return "\n".join(out)


def to_text(report: Dict[str, Any]) -> str:
    lines = []
    for check in report["checks"]:
        lines.append("[{:<5}] {:<28} {}".format(check["status"].upper(), check["title"], check["summary"]))
    summary = report["summary"]
    counts = ", ".join("{} {}".format(v, k) for k, v in summary["counts"].items() if v)
    lines.append("")
    if summary.get("headline"):
        lines.append(summary["headline"])
    lines.append("Overall: {} ({})".format(summary["overall_status"].upper(), counts or "no checks"))
    if report["data_source"] != "live":
        lines.append("NOTE: synthetic data from {}".format(report["data_source"]))
    return "\n".join(lines) + "\n"
