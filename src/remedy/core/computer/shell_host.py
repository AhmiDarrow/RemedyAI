"""Thin Python bindings for Zig shell-host (prepare + script helpers).

Zig owns IR prepare/translate/scriptfile, HostSession, ConPTY, dialect, and
policy. Go owns production ``/api/terminal``. This module is HostOp
serialization + prepare/script helpers only. ``HostSession`` / shared-session
orchestration and ConPTY live in ``host_binding``. ``resolve_which`` /
``default_script_lang`` live in ``host_binding``.
"""

from __future__ import annotations

import os
import shlex
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from remedy.core.computer.host_binding import (
    HostSession,
    SessionResult,
    close_all_shared_sessions,
    close_shared_session,
    default_script_lang,
    get_shared_session,
    resolve_which,
    spawn_conpty_supported as conpty_available,
)

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


_MAX_SCRIPT_CHARS = 1_000_000
_HOST_SCRIPT_PREFIX = "host_"
_HOST_SCRIPT_MAX_AGE_S = 3600.0


@dataclass
class ScriptLaunch:
    argv: list[str]
    path: Path
    lang: str
    body: str


def cleanup_host_script(path: Path | None) -> None:
    """Delete a scratch host_* script after it has run."""
    if path is None:
        return
    p = Path(path)
    if not p.is_file() or not p.name.startswith(_HOST_SCRIPT_PREFIX):
        return
    with suppress(OSError):
        p.unlink()


def age_out_host_scripts(
    scratch_dir: Path | None = None,
    *,
    max_age_s: float = _HOST_SCRIPT_MAX_AGE_S,
) -> int:
    """Remove leftover host_* scratch files older than *max_age_s*."""
    import time

    roots: list[Path] = []
    if scratch_dir is not None:
        roots.append(Path(scratch_dir))
    else:
        for envk in ("TEMP", "TMP"):
            raw = os.environ.get(envk)
            if raw:
                roots.append(Path(raw) / "remedy-host")
        roots.append(Path(".remedy-build") / "tmp")
    removed = 0
    now = time.time()
    for root in roots:
        if not root.is_dir():
            continue
        try:
            kids = list(root.iterdir())
        except OSError:
            continue
        for kid in kids:
            if not kid.is_file() or not kid.name.startswith(_HOST_SCRIPT_PREFIX):
                continue
            try:
                if now - kid.stat().st_mtime > max_age_s:
                    kid.unlink()
                    removed += 1
            except OSError:
                continue
    return removed


def launch_script(
    lang: str,
    body: str,
    *,
    scratch_dir: Path | None = None,
    project_path: str | Path | None = None,
) -> ScriptLaunch:
    """Write a scratch script via Zig ``host_op_prepare`` (never ``-Command``).

    Python argv uses ``python_cmd_for_subprocess`` so the Desktop sidecar is
    never re-launched as the interpreter (Zig PATH resolve alone is not enough).
    """
    text = body or ""
    if len(text) > _MAX_SCRIPT_CHARS:
        raise ValueError(f"script body exceeds {_MAX_SCRIPT_CHARS} characters")
    kind = (lang or "pwsh").lower()
    if kind in ("powershell", "ps1"):
        kind = "pwsh"
    from remedy.core.computer.host_binding import HostError

    try:
        prep = prepare_host_op(
            script_op(kind, text),
            scratch_dir=scratch_dir,
            project_path=project_path,
        )
    except HostError as exc:
        if kind == "python":
            raise ValueError(
                "No real Python interpreter for host_script "
                "(Desktop sidecar is not CPython). Set REMEDY_PYTHON."
            ) from exc
        raise ValueError(f"host script prepare failed: {exc}") from exc
    if prep.kind != "script" or prep.script_path is None:
        raise ValueError("host script prepare did not produce a script file")
    path = Path(prep.script_path)
    age_out_host_scripts(path.parent)
    argv = list(prep.argv)
    out_lang = str(prep.ir.lang or kind)
    if kind == "python" or out_lang == "python":
        from remedy.core.build_python import python_cmd_for_subprocess

        py = python_cmd_for_subprocess()
        if not py:
            cleanup_host_script(path)
            raise ValueError(
                "No real Python interpreter for host_script "
                "(Desktop sidecar is not CPython). Set REMEDY_PYTHON."
            )
        argv = [*py, str(path)]
        out_lang = "python"
    return ScriptLaunch(argv=argv, path=path, lang=out_lang, body=text)


__all__ = [
    "HostOp",
    "HostSession",
    "PreparedCommand",
    "ScriptLaunch",
    "SessionResult",
    "age_out_host_scripts",
    "cleanup_host_script",
    "close_all_shared_sessions",
    "close_shared_session",
    "coerce_argv",
    "conpty_available",
    "default_script_lang",
    "get_shared_session",
    "launch_script",
    "mkdir_op",
    "prepare_host_command",
    "prepare_host_op",
    "raw_op",
    "resolve_which",
    "run_op",
    "script_op",
    "which_op",
]
