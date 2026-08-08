"""Error-vector extraction from verify output — machine repair tickets.

After a red verify, the organism should not free-form guess. It gets a
structured falsification vector: failing tests, path:line, short stderr.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# pytest: tests/test_x.py::test_foo FAILED
_PYTEST_FAIL = re.compile(
    r"(?m)^(?:FAILED\s+)?([\w./\\-]+\.py)(?::(\d+))?(?:::\S+)?\s*(?:FAILED|ERROR)?"
)
_PYTEST_NODE = re.compile(
    r"(?m)^(?:FAILED|ERROR)\s+([\w./\\-]+\.py(?:::\S+)?)"
)
# path:line: message (mypy, ruff, tsc-ish)
_PATH_LINE = re.compile(
    r"(?m)([A-Za-z]:\\[^\s:]+|/[^\s:]+|[\w./\\-]+\.(?:py|ts|tsx|js|rs|go))(?::(\d+))+"
)
# npm / jest
_JEST_FAIL = re.compile(r"(?m)●\s+(.+)$")
# cargo
_CARGO_ERR = re.compile(r"(?m)^error(?:\[E\d+\])?:\s+(.+)$")


@dataclass
class ErrorVector:
    """Compressed falsification signal for the repair phase."""

    ok: bool = False
    command: str = ""
    exit_hint: str = ""
    failing_nodes: list[str] = field(default_factory=list)
    path_lines: list[str] = field(default_factory=list)
    snippets: list[str] = field(default_factory=list)
    raw_tail: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "exit_hint": self.exit_hint,
            "failing_nodes": self.failing_nodes[:20],
            "path_lines": self.path_lines[:24],
            "snippets": self.snippets[:12],
        }


def parse_verify_output(
    summary: str,
    *,
    command: str = "",
    ok: bool | None = None,
) -> ErrorVector:
    """Parse tool/verify summary text into an ErrorVector."""
    text = summary or ""
    low = text.lower()
    if ok is None:
        if "exit_code=0" in low or (
            re.search(r"\bpassed\b", low) and "failed" not in low[:400]
        ):
            ok = True
        elif "exit_code=" in low or "FAILED" in text or "ERROR" in text[:200]:
            ok = False
        else:
            ok = False

    vec = ErrorVector(ok=bool(ok), command=command or "", raw_tail=text[-1500:])
    m = re.search(r"exit_code=(-?\d+)", low)
    if m:
        vec.exit_hint = f"exit_code={m.group(1)}"

    if ok:
        return vec

    seen_n: set[str] = set()
    for m in _PYTEST_NODE.finditer(text):
        node = m.group(1).strip()
        if node and node not in seen_n:
            seen_n.add(node)
            vec.failing_nodes.append(node)
    for m in _PYTEST_FAIL.finditer(text):
        path = m.group(1)
        line = m.group(2)
        key = f"{path}:{line}" if line else path
        if key not in seen_n:
            seen_n.add(key)
            if path not in vec.failing_nodes:
                vec.failing_nodes.append(path)

    seen_pl: set[str] = set()
    for m in _PATH_LINE.finditer(text):
        path = m.group(1)
        line = m.group(2) or ""
        key = f"{path}:{line}" if line else path
        if key in seen_pl:
            continue
        seen_pl.add(key)
        vec.path_lines.append(key)
        if len(vec.path_lines) >= 24:
            break

    for m in _JEST_FAIL.finditer(text):
        s = m.group(1).strip()[:160]
        if s and s not in vec.snippets:
            vec.snippets.append(s)
    for m in _CARGO_ERR.finditer(text):
        s = m.group(1).strip()[:160]
        if s and s not in vec.snippets:
            vec.snippets.append(s)

    # Last non-empty error-ish lines
    for line in reversed(text.splitlines()):
        ls = line.strip()
        if not ls or len(ls) < 8:
            continue
        if re.search(r"(?i)error|fail|assert|traceback|exception", ls):
            if ls not in vec.snippets:
                vec.snippets.append(ls[:200])
            if len(vec.snippets) >= 8:
                break

    return vec


def format_repair_ticket(vec: ErrorVector) -> str:
    """User-role content: machine repair ticket the model must execute."""
    if vec.ok:
        return (
            "[Build engine · ERROR VECTOR · GREEN]\n"
            f"command=`{vec.command}` {vec.exit_hint}\n"
            "No failures. You may close the build if the goal is met."
        )
    lines = [
        "[Build engine · ERROR VECTOR · REPAIR TICKET]",
        f"command=`{vec.command}` {vec.exit_hint or 'FAILED'}",
        "Machine schedule: fix ONLY these failures, then re-verify with the SAME command.",
        "Do not expand scope. Do not claim success until exit_code=0.",
    ]
    if vec.failing_nodes:
        lines.append("Failing nodes:")
        for n in vec.failing_nodes[:12]:
            lines.append(f"  · {n}")
    if vec.path_lines:
        lines.append("path:line hotspots:")
        for p in vec.path_lines[:16]:
            lines.append(f"  · {p}")
    if vec.snippets:
        lines.append("Signals:")
        for s in vec.snippets[:6]:
            lines.append(f"  · {s}")
    lines.append(
        "Next tool_calls: file_read the hotspots → file_edit multi-hunk → "
        "bash_exec / job_run kind=verify with the same command."
    )
    return "\n".join(lines)


def repair_ticket_message(vec: ErrorVector) -> dict[str, str]:
    return {"role": "user", "content": format_repair_ticket(vec)}
