"""HostOp serialization + thin Zig prepare wrappers.

Zig owns IR prepare/translate/scriptfile, HostSession, ConPTY, dialect, and
policy. Go owns production ``/api/terminal``. Script launch helpers and
``HostSession`` / shared-session live in ``host_binding``.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

OpKind = Literal["run", "mkdir", "which", "env", "script", "raw", "chain"]


@dataclass
class HostOp:
    """One host operation. ``chain`` holds sequential child ops."""

    kind: OpKind
    argv: list[str] = field(default_factory=list)
    paths: list[str] = field(default_factory=list)
    name: str = ""
    lang: str = ""
    body: str = ""
    host: str = ""
    text: str = ""
    cwd: str = ""
    env: dict[str, str] = field(default_factory=dict)
    ops: list[HostOp] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.argv:
            d["argv"] = list(self.argv)
        if self.paths:
            d["paths"] = list(self.paths)
        if self.name:
            d["name"] = self.name
        if self.lang:
            d["lang"] = self.lang
        if self.body:
            d["body"] = self.body
        if self.host:
            d["host"] = self.host
        if self.text:
            d["text"] = self.text
        if self.cwd:
            d["cwd"] = self.cwd
        if self.env:
            d["env"] = dict(self.env)
        if self.ops:
            d["ops"] = [o.to_dict() for o in self.ops]
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> HostOp:
        if not isinstance(data, dict):
            return cls(kind="raw", text="")
        kind = str(data.get("kind") or "raw")
        if kind not in ("run", "mkdir", "which", "env", "script", "raw", "chain"):
            kind = "raw"
        children = data.get("ops") or []
        ops = [cls.from_dict(c) for c in children if isinstance(c, dict)]
        env_raw = data.get("env") or {}
        env = (
            {str(k): str(v) for k, v in env_raw.items()}
            if isinstance(env_raw, dict)
            else {}
        )
        return cls(
            kind=kind,  # type: ignore[arg-type]
            argv=[str(a) for a in (data.get("argv") or [])],
            paths=[str(p) for p in (data.get("paths") or [])],
            name=str(data.get("name") or ""),
            lang=str(data.get("lang") or ""),
            body=str(data.get("body") or ""),
            host=str(data.get("host") or ""),
            text=str(data.get("text") or ""),
            cwd=str(data.get("cwd") or ""),
            env=env,
            ops=ops,
        )


def run_op(argv: list[str], *, cwd: str = "") -> HostOp:
    return HostOp(kind="run", argv=[str(a) for a in argv if str(a)], cwd=cwd)


def mkdir_op(paths: list[str], *, cwd: str = "") -> HostOp:
    return HostOp(kind="mkdir", paths=[str(p) for p in paths if str(p)], cwd=cwd)


def which_op(name: str) -> HostOp:
    return HostOp(kind="which", name=str(name or "").strip())


def script_op(lang: str, body: str, *, cwd: str = "") -> HostOp:
    return HostOp(kind="script", lang=str(lang or "pwsh"), body=str(body or ""), cwd=cwd)


def raw_op(text: str, *, host: str = "") -> HostOp:
    return HostOp(kind="raw", text=str(text or ""), host=host)


@dataclass
class PreparedCommand:
    argv: list[str]
    display: str
    kind: str  # argv | script | translated | raw | session
    ir: HostOp
    script_path: Path | None = None
    notes: list[str] = field(default_factory=list)
    translated: str = ""
    host: str = "cmd"


def coerce_argv(argv: Any) -> list[str]:
    """Accept a list, a JSON list string, or a single command string."""
    if argv is None:
        return []
    if isinstance(argv, (list, tuple)):
        return [str(a) for a in argv if str(a)]
    text = str(argv).strip()
    if not text:
        return []
    if text.startswith("[") and text.endswith("]"):
        try:
            import json

            parsed = json.loads(text)
            if isinstance(parsed, list):
                return [str(a) for a in parsed if str(a)]
        except (json.JSONDecodeError, TypeError, ValueError):
            pass
    posix = os.name != "nt"
    try:
        return [t for t in shlex.split(text, posix=posix) if t]
    except ValueError:
        return text.split()


def _prepared_from_native(data: dict[str, Any]) -> PreparedCommand:
    """Build a :class:`PreparedCommand` from ``remedy_core_host_op_prepare`` JSON."""
    ir_raw = data.get("ir")
    script = data.get("script_path") or None
    notes_raw = data.get("notes") or []
    return PreparedCommand(
        argv=[str(a) for a in (data.get("argv") or []) if str(a)],
        display=str(data.get("display") or ""),
        kind=str(data.get("kind") or "raw"),
        ir=HostOp.from_dict(ir_raw if isinstance(ir_raw, dict) else {}),
        script_path=Path(str(script)) if script else None,
        notes=[str(n) for n in notes_raw] if isinstance(notes_raw, list) else [],
        translated=str(data.get("translated") or ""),
        host=str(data.get("host") or ("cmd" if os.name == "nt" else "posix")),
    )


def prepare_host_command(
    command: str,
    *,
    scratch_dir: Path | None = None,
    project_path: str | Path | None = None,
    host: str | None = None,
) -> PreparedCommand:
    """Turn a model-emitted command string into an argv the sandbox can exec.

    Implemented in Zig (``remedy_core_host_op_prepare`` with ``command``).
    No Python rewrite twin — untranslatable substitutions raise ``ValueError``.
    """
    from remedy.core.computer.host_binding import (
        STATUS_INVALID_ARGUMENT,
        HostError,
        host_op_prepare,
    )

    payload: dict[str, Any] = {"command": command or ""}
    if host is not None:
        payload["host"] = host
    if scratch_dir is not None:
        payload["scratch_dir"] = str(scratch_dir)
    if project_path is not None:
        payload["project_path"] = str(project_path)
    try:
        return _prepared_from_native(host_op_prepare(raw=payload))
    except HostError as exc:
        if exc.status == STATUS_INVALID_ARGUMENT:
            raise ValueError(
                "untranslatable substitution $(…) / backticks / ${} — use host_script"
            ) from exc
        raise


def prepare_host_op(
    op: HostOp,
    *,
    scratch_dir: Path | None = None,
    project_path: str | Path | None = None,
) -> PreparedCommand:
    """Prepare argv from a structured HostOp (no command-string parsing).

    All kinds including ``raw`` go through ``remedy_core_host_op_prepare``
    (Zig). ConPTY is ABI 5 via ``host_binding``. Process policy is Zig;
    tool allow/ask/deny is ``PolicyEngine``.
    """
    from remedy.core.computer.host_binding import (
        STATUS_INVALID_ARGUMENT,
        HostError,
        host_op_prepare,
    )

    try:
        return _prepared_from_native(
            host_op_prepare(
                op=op.to_dict(),
                scratch_dir=str(scratch_dir) if scratch_dir else None,
                project_path=str(project_path) if project_path else None,
            )
        )
    except HostError as exc:
        if op.kind == "raw" and exc.status == STATUS_INVALID_ARGUMENT:
            raise ValueError(
                "untranslatable substitution $(…) / backticks / ${} — use host_script"
            ) from exc
        raise


__all__ = [
    "HostOp",
    "PreparedCommand",
    "coerce_argv",
    "mkdir_op",
    "prepare_host_command",
    "prepare_host_op",
    "raw_op",
    "run_op",
    "script_op",
    "which_op",
]
