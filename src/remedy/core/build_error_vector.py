"""Error-vector extraction from verify output — machine repair tickets.

After a red verify, the organism should not free-form guess. It gets a
structured falsification vector: failing tests, path:line, short stderr.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

# pytest short-summary: FAILED tests/test_x.py::test_foo - assert 1 == 2
_PYTEST_NODE = re.compile(
    r"(?m)^(?:FAILED|ERROR)\s+((?:[A-Za-z]:\\)?[\w./\\-]+\.py(?:::\S+)?)"
)
_PYTEST_NODE_REASON = re.compile(
    r"(?m)^(?:FAILED|ERROR)\s+((?:[A-Za-z]:\\)?[\w./\\-]+\.py(?:::\S+)?)\s+-\s+(.+)$"
)
# pytest -q one-liner: tests/test_x.py::test_foo FAILED
_PYTEST_Q = re.compile(
    r"(?m)^((?:[A-Za-z]:\\)?[\w./\\-]+\.py(?:::\S+)?)\s+(?:FAILED|ERROR)\b"
)
# pytest E-prefix assertion / exception lines
_PYTEST_E = re.compile(r"(?m)^E\s+(.+)$")
# CPython traceback
_TRACEBACK_FILE = re.compile(
    r'(?m)^\s*File\s+"([^"]+\.(?:py|pyw))",\s+line\s+(\d+)'
)
# gcc / clang / rustc: file:line[:col]: error: msg
_COMPILER_ERR = re.compile(
    r"(?m)^((?:[A-Za-z]:\\)?[^\s:]+?\.(?:c|cc|cpp|cxx|h|hpp|rs|go)):(\d+)(?::\d+)?:\s*"
    r"(?:error|fatal error):\s*(.+)$"
)
# path:line (mypy, ruff, tsc-ish)
_PATH_LINE = re.compile(
    r"(?m)([A-Za-z]:\\[^\s:]+|/[^\s:]+|[\w./\\-]+\.(?:py|ts|tsx|js|rs|go|c|h))(?::(\d+))+"
)
_JEST_FAIL = re.compile(r"(?m)●\s+(.+)$")
_CARGO_ERR = re.compile(r"(?m)^error(?:\[E\d+\])?:\s+(.+)$")
_IMPORT_ERR = re.compile(
    r"(?m)(?:ModuleNotFoundError|ImportError):\s+No module named ['\"]([^'\"]+)['\"]"
)


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
    repair_command: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "command": self.command,
            "exit_hint": self.exit_hint,
            "failing_nodes": self.failing_nodes[:20],
            "path_lines": self.path_lines[:24],
            "snippets": self.snippets[:12],
            "repair_command": self.repair_command,
        }

    @classmethod
    def from_public(cls, raw: dict[str, Any] | None) -> ErrorVector:
        d = raw if isinstance(raw, dict) else {}
        return cls(
            ok=bool(d.get("ok")),
            command=str(d.get("command") or ""),
            exit_hint=str(d.get("exit_hint") or ""),
            failing_nodes=[str(x) for x in (d.get("failing_nodes") or []) if x],
            path_lines=[str(x) for x in (d.get("path_lines") or []) if x],
            snippets=[str(x) for x in (d.get("snippets") or []) if x],
            repair_command=str(d.get("repair_command") or ""),
        )


def parse_verify_output(
    summary: str,
    *,
    command: str = "",
    ok: bool | None = None,
) -> ErrorVector:
    """Parse tool/verify summary text into an ErrorVector."""
    text = summary or ""
    official = re.search(r"(?m)^(verify )?exit_code=(-?\d+)\s*$", text)
    if ok is None:
        ok = official.group(2) == "0" if official else False

    vec = ErrorVector(ok=bool(ok), command=command or "", raw_tail=text[-1500:])
    if official:
        vec.exit_hint = f"exit_code={official.group(2)}"

    if ok:
        return vec

    def _add_node(node: str) -> None:
        n = (node or "").strip()
        if n and n not in vec.failing_nodes:
            vec.failing_nodes.append(n)

    def _add_pl(path: str, line: str = "") -> None:
        path = (path or "").strip()
        if not path:
            return
        key = f"{path}:{line}" if line else path
        if key not in vec.path_lines:
            vec.path_lines.append(key)

    def _add_snip(s: str) -> None:
        s = (s or "").strip()
        if s and s not in vec.snippets:
            vec.snippets.append(s[:200])

    for m in _PYTEST_NODE_REASON.finditer(text):
        _add_node(m.group(1))
        _add_snip(m.group(2))
    for m in _PYTEST_NODE.finditer(text):
        _add_node(m.group(1))
    for m in _PYTEST_Q.finditer(text):
        _add_node(m.group(1))
    for m in _PYTEST_E.finditer(text):
        _add_snip(m.group(1))
    for m in _TRACEBACK_FILE.finditer(text):
        _add_pl(m.group(1), m.group(2))
    for m in _COMPILER_ERR.finditer(text):
        _add_pl(m.group(1), m.group(2))
        _add_snip(m.group(3))
    for m in _IMPORT_ERR.finditer(text):
        _add_snip(f"missing module '{m.group(1)}'")

    seen_pl = set(vec.path_lines)
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
        _add_snip(m.group(1))
    for m in _CARGO_ERR.finditer(text):
        _add_snip(m.group(1))

    for line in reversed(text.splitlines()):
        ls = line.strip()
        if not ls or len(ls) < 8:
            continue
        if re.search(r"(?i)error|fail|assert|traceback|exception", ls):
            _add_snip(ls)
            if len(vec.snippets) >= 10:
                break

    vec.repair_command = scoped_pytest_from_nodes(
        vec.failing_nodes, base=command or ""
    )
    return vec


def scoped_pytest_from_nodes(nodes: list[str], *, base: str = "") -> str:
    """Smallest pytest command that still hits the failing nodeids."""
    ids: list[str] = []
    for raw in nodes or []:
        n = str(raw or "").strip().replace("\\", "/")
        if ".py" not in n.lower():
            continue
        # drop trailing :line from path:line (keep ::test)
        if "::" not in n:
            n = re.sub(r":\d+$", "", n)
        # Windows drive: C:/proj/tests/foo.py::test — keep as-is
        if n not in ids:
            ids.append(n)
        if len(ids) >= 16:
            break
    if not ids:
        return ""
    prefix = "pytest -q"
    b = (base or "").strip()
    if re.match(r"(?i)^\s*uv\s+run\s+pytest\b", b):
        prefix = "uv run pytest -q"
    elif re.match(r"(?i)^\s*python(?:3)?\s+-m\s+pytest\b", b):
        prefix = "python -m pytest -q"
    quoted = [f'"{i}"' if " " in i else i for i in ids]
    return prefix + " " + " ".join(quoted)


def format_repair_ticket(vec: ErrorVector) -> str:
    """User-role content: machine repair ticket the model must execute."""
    if vec.ok:
        return (
            "[Build engine · ERROR VECTOR · GREEN]\n"
            f"command=`{vec.command}` {vec.exit_hint}\n"
            "No failures. You may close the build if the goal is met."
        )
    next_cmd = (
        vec.repair_command
        or scoped_pytest_from_nodes(vec.failing_nodes, base=vec.command)
        or vec.command
        or ""
    ).strip()
    lines = [
        "[Build engine · ERROR VECTOR · REPAIR TICKET]",
        f"command=`{vec.command}` {vec.exit_hint or 'FAILED'}",
        "Machine schedule: fix ONLY these failures. Do not expand scope.",
        "Do not claim success until the next verify exits 0.",
    ]
    if next_cmd:
        lines.append(f"NEXT VERIFY: `{next_cmd}`")
    if vec.failing_nodes:
        lines.append("Failing nodes:")
        for n in vec.failing_nodes[:12]:
            lines.append(f"  · {n}")
    read_first = ""
    if vec.path_lines:
        lines.append("path:line hotspots:")
        for p in vec.path_lines[:16]:
            lines.append(f"  · {p}")
        read_first = re.sub(r":\d+$", "", vec.path_lines[0])
    elif vec.failing_nodes:
        read_first = vec.failing_nodes[0].split("::", 1)[0]
    if vec.snippets:
        lines.append("Signals:")
        for s in vec.snippets[:6]:
            lines.append(f"  · {s}")
    if read_first:
        lines.append(f"READ FIRST: `{read_first}`")
    lines.append(
        "Next tool_calls (this step): file_read the READ FIRST path → "
        "file_edit multi-hunk on the implementation (not a rewrite) → "
        f"bash_exec / job_run kind=verify `{next_cmd or vec.command or 'pytest -q'}`."
    )
    return "\n".join(lines)


def repair_ticket_message(vec: ErrorVector) -> dict[str, str]:
    return {"role": "user", "content": format_repair_ticket(vec)}
