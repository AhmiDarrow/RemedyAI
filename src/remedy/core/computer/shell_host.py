"""Thin Python bindings for Zig shell-host (prepare, HostSession, scripts).

Zig ``remedy_core`` owns Host Command IR prepare/translate/scriptfile,
HostSession open/run/cwd/close, ConPTY, dialect, and policy. This module is
serialization + asyncio orchestration only — no Python twins of Zig logic.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from remedy.core.computer.host_binding import (
    looks_like_powershell,
    translate_posix_to_host,
)

HostKind = Literal["cmd", "pwsh", "posix"]
OpKind = Literal["run", "mkdir", "which", "env", "script", "raw", "chain"]
ScriptLang = Literal["pwsh", "cmd", "python"]


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
        ops = [
            cls.from_dict(c)
            for c in children
            if isinstance(c, dict)
        ]
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


def _ok_python(path: str | None) -> bool:
    if not path:
        return False
    from remedy.core.build_python import is_usable_host_python

    return is_usable_host_python(path)


def resolve_which(name: str, *, cwd: Path | str | None = None) -> str | None:
    """Resolve an executable the way the host would.

    Project ``.venv`` / ``node_modules/.bin`` first, then Zig dialect +
    ``resolveWhich`` via ``host_op_prepare``. No ``shutil.which`` soft twin —
    native runtime errors fail closed.
    """
    n = (name or "").strip()
    if not n:
        return None
    key = n.lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if key.endswith(".exe"):
        key = key[:-4]

    if cwd is not None:
        try:
            from remedy.core.project_fingerprint import local_bin_dirs

            suffix = ".exe" if os.name == "nt" else ""
            for bin_dir in local_bin_dirs(cwd):
                cand = bin_dir / (
                    n + suffix if suffix and not n.lower().endswith(suffix) else n
                )
                if cand.is_file():
                    return str(cand)
                if suffix:
                    alt = bin_dir / f"{key}{suffix}"
                    if alt.is_file():
                        return str(alt)
        except OSError:
            pass

    from remedy.core.computer import host_binding

    d = host_binding.dialect_load("")
    mapped = {
        "python": str(d.get("python_cmd") or ""),
        "python3": str(d.get("python_cmd") or ""),
        "py": str(d.get("python_cmd") or ""),
        "git": str(d.get("git_cmd") or ""),
        "rg": str(d.get("rg_cmd") or ""),
        "pwsh": str(d.get("pwsh_cmd") or ""),
    }
    hit = (mapped.get(key) or "").strip()
    if hit and Path(hit).is_file():
        if key in {"python", "python3", "py"}:
            if _ok_python(hit):
                return hit
        else:
            return hit

    prep = prepare_host_op(run_op([n]), project_path=cwd)
    if prep.argv:
        cand = str(prep.argv[0] or "")
        path = Path(cand)
        if cand and path.is_file() and path.is_absolute():
            if key in {"python", "python3", "py"}:
                if _ok_python(cand):
                    return cand
            else:
                return cand

    if key in {"python", "python3"}:
        try:
            from remedy.core.build_python import host_python_executable

            found = host_python_executable()
        except OSError:
            found = ""
        if found and _ok_python(found):
            return found
        from remedy.core.runtime_identity import is_frozen_install

        if is_frozen_install():
            return None
        exe = sys.executable or ""
        return exe if _ok_python(exe) else None
    return None


def default_script_lang(home: str | Path | None = None) -> str:
    """pwsh when Zig dialect finds it; otherwise python (POSIX) or cmd.

    No ``shutil.which`` soft twin — dialect errors fail closed.
    """
    if os.name != "nt":
        return "python"
    from remedy.core.computer import host_binding

    d = host_binding.dialect_load(str(home) if home else "")
    pwsh = str(d.get("pwsh_cmd") or "").strip()
    if pwsh and Path(pwsh).is_file():
        return "pwsh"
    return "cmd"


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


# --- Persistent HostSession (Zig ABI 5) --------------------------------------

_SESSIONS_GUARD = asyncio.Lock()


@dataclass
class SessionResult:
    exit_code: int
    stdout: str
    stderr: str = ""
    cwd: str = ""
    timed_out: bool = False
    interactive: bool = False
    host: str = "cmd"
    used_conpty: bool = False


@dataclass
class HostSession:
    """One long-lived cmd/pwsh process. Not the default for bash_exec.

    Windows: Zig ``remedy_core`` HostSession owns spawn + sentinel I/O.
    Non-Windows: fail closed (no soft pipe twin).
    """

    host: str = "cmd"
    cwd: str | None = None
    env: dict[str, str] | None = None
    use_conpty: bool = False
    _zig_handle: int = field(default=0, init=False, repr=False)
    _lock: asyncio.Lock | None = field(default=None, init=False, repr=False)
    started: bool = field(default=False, init=False)
    _used_conpty: bool = field(default=False, init=False, repr=False)

    async def start(self) -> None:
        if self.started and self._alive():
            return
        self._lock = asyncio.Lock()
        if os.name != "nt":
            from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError

            raise HostError("host_session_open", STATUS_UNSUPPORTED)
        await self._start_zig()

    async def _start_zig(self) -> None:
        """Windows: Zig HostSession owns spawn + sentinel I/O (authorized open)."""
        from remedy.core.computer import host_binding

        if self.env is not None:
            env = dict(self.env)
        else:
            from remedy.execution.env import scrub_subprocess_env

            env = scrub_subprocess_env()
        token, now_ms = host_binding.issue_host_session_token(self.host)
        handle = await asyncio.to_thread(
            host_binding.host_session_open_authorized,
            host=self.host,
            cwd=self.cwd,
            env=env,
            use_conpty=bool(self.use_conpty),
            token=token,
            now_ms=now_ms,
        )
        self._zig_handle = int(handle)
        self.started = True
        self._used_conpty = bool(self.use_conpty)

    async def run(self, command: str, *, timeout: float = 60.0) -> SessionResult:
        if not command or not str(command).strip():
            return SessionResult(exit_code=-1, stdout="", stderr="empty command", host=self.host)
        await self.start()
        assert self._lock is not None
        if not self._zig_handle:
            from remedy.core.computer.host_binding import STATUS_UNSUPPORTED, HostError

            raise HostError("host_session_run", STATUS_UNSUPPORTED)
        return await self._run_zig(command.strip(), timeout=timeout)

    async def _run_zig(self, command: str, *, timeout: float) -> SessionResult:
        from remedy.core.computer import host_binding

        assert self._lock is not None
        async with self._lock:
            data = await asyncio.to_thread(
                host_binding.host_session_run,
                self._zig_handle,
                command,
                timeout_ms=int(max(1.0, float(timeout)) * 1000),
            )
        timed_out = bool(data.get("timed_out"))
        interactive = bool(data.get("interactive"))
        if timed_out or interactive:
            self._abandon_proc()
        return SessionResult(
            exit_code=int(data.get("exit_code", -1)),
            stdout=str(data.get("stdout") or ""),
            stderr=str(data.get("stderr") or ""),
            cwd=str(data.get("cwd") or ""),
            timed_out=timed_out,
            interactive=interactive,
            host=str(data.get("host") or self.host),
            used_conpty=bool(data.get("used_conpty", self._used_conpty)),
        )

    async def current_cwd(self) -> str:
        if not self._alive() or not self._zig_handle:
            return ""
        from remedy.core.computer import host_binding

        assert self._lock is not None
        async with self._lock:
            here = await asyncio.to_thread(
                host_binding.host_session_cwd, self._zig_handle
            )
        if not here:
            self._abandon_proc()
        return here or ""

    async def close(self) -> None:
        handle = self._zig_handle
        self._zig_handle = 0
        self.started = False
        if handle:
            from remedy.core.computer import host_binding

            await asyncio.to_thread(host_binding.host_session_close, handle)

    def _abandon_proc(self) -> None:
        handle = self._zig_handle
        self._zig_handle = 0
        self.started = False
        if handle:
            with suppress(Exception):
                from remedy.core.computer import host_binding

                host_binding.host_session_close(handle)

    def _alive(self) -> bool:
        return bool(self._zig_handle) and self.started


def conpty_available() -> bool:
    """True when ``remedy_core`` reports ConPTY (ABI 5) on this host."""
    if sys.platform != "win32":
        return False
    try:
        from remedy.core.computer import host_binding

        return bool(host_binding.conpty_available())
    except Exception:
        return False


_SESSIONS: dict[str, HostSession] = {}


def _session_key(session_id: str | None) -> str:
    sid = (session_id or "").strip()
    return sid if sid else "_GLOBAL"


def _norm_cwd_key(cwd: str | None) -> str:
    if not cwd:
        return ""
    try:
        return (
            str(Path(cwd).expanduser().resolve(strict=False))
            .replace("\\", "/")
            .rstrip("/")
            .lower()
        )
    except OSError:
        return cwd.replace("\\", "/").rstrip("/").lower()


async def get_shared_session(
    *,
    host: str | None = None,
    cwd: str | None = None,
    env: dict[str, str] | None = None,
    use_conpty: bool = False,
    session_id: str | None = None,
) -> HostSession:
    want = host or ("cmd" if os.name == "nt" else "posix")
    key = _session_key(session_id)
    to_close: HostSession | None = None
    async with _SESSIONS_GUARD:
        existing = _SESSIONS.get(key)
        if existing is not None and existing.host == want and existing._alive():
            if cwd and existing.cwd and _norm_cwd_key(cwd) != _norm_cwd_key(existing.cwd):
                to_close = _SESSIONS.pop(key, None)
            else:
                return existing
        elif existing is not None:
            to_close = _SESSIONS.pop(key, None)
    if to_close is not None:
        await to_close.close()
    sess = HostSession(host=want, cwd=cwd, env=env, use_conpty=use_conpty)
    await sess.start()
    extra_close: HostSession | None = None
    async with _SESSIONS_GUARD:
        other = _SESSIONS.get(key)
        if other is not None and other.host == want and other._alive():
            extra_close = sess
            sess = other
        else:
            _SESSIONS[key] = sess
    if extra_close is not None:
        await extra_close.close()
    return sess


async def close_shared_session(session_id: str | None = None) -> None:
    """Close the keyed session. None/empty closes only the default ``_GLOBAL`` session."""
    async with _SESSIONS_GUARD:
        sess = _SESSIONS.pop(_session_key(session_id), None)
    if sess is not None:
        await sess.close()


async def close_all_shared_sessions() -> None:
    async with _SESSIONS_GUARD:
        items = list(_SESSIONS.items())
        _SESSIONS.clear()
    for _key, sess in items:
        if sess is not None:
            await sess.close()


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
    "looks_like_powershell",
    "mkdir_op",
    "prepare_host_command",
    "prepare_host_op",
    "raw_op",
    "resolve_which",
    "run_op",
    "script_op",
    "translate_posix_to_host",
    "which_op",
]
