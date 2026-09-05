"""Prepare a host command: Zig owns prepare/scriptfile and shell-chain expand."""

from __future__ import annotations

import os
import shlex
import shutil
import sys
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from remedy.execution.host.ir import HostOp, script_op


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


@dataclass(frozen=True)
class ChainHop:
    """One hop of an ``A && B`` chain the sandbox can run without cmd.exe."""

    kind: Literal["run", "cd", "mkdir"]
    argv: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()

    @staticmethod
    def run(argv: list[str]) -> ChainHop:
        return ChainHop(kind="run", argv=tuple(argv))

    @staticmethod
    def cd(path: str) -> ChainHop:
        return ChainHop(kind="cd", paths=(path,))

    @staticmethod
    def mkdir(paths: list[str]) -> ChainHop:
        return ChainHop(kind="mkdir", paths=tuple(paths))

    @staticmethod
    def from_dict(raw: dict[str, Any]) -> ChainHop:
        kind = str(raw.get("kind") or "")
        if kind == "run":
            return ChainHop.run([str(a) for a in (raw.get("argv") or [])])
        if kind == "cd":
            paths = [str(p) for p in (raw.get("paths") or [])]
            return ChainHop.cd(paths[0] if paths else "")
        if kind == "mkdir":
            return ChainHop.mkdir([str(p) for p in (raw.get("paths") or [])])
        raise ValueError(f"unknown chain hop kind: {kind!r}")


def expand_shell_chain(
    argv: list[str],
    *,
    project_path: Path | str | None = None,
) -> list[ChainHop] | None:
    """Turn ``cmd /c A && B`` into hidden cd/mkdir/run hops (Zig only)."""
    from remedy.core.computer.host_binding import HostError, shell_chain_expand

    payload: dict[str, Any] = {"argv": [str(a) for a in argv]}
    if project_path is not None:
        payload["project_path"] = str(project_path)
    try:
        result = shell_chain_expand(payload)
    except HostError:
        raise
    hops_raw = result.get("hops")
    if not hops_raw:
        return None
    hops: list[ChainHop] = []
    for item in hops_raw:
        if not isinstance(item, dict):
            return None
        try:
            hops.append(ChainHop.from_dict(item))
        except (TypeError, ValueError):
            return None
    return hops if len(hops) >= 2 else None


def expand_and_chain_argv(
    argv: list[str],
    *,
    project_path: Path | str | None = None,
) -> list[list[str]] | None:
    """If *argv* is ``cmd /c A && B`` of plain processes, return those argvs."""
    hops = expand_shell_chain(argv, project_path=project_path)
    if not hops or any(h.kind != "run" for h in hops):
        return None
    return [list(h.argv) for h in hops]


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


def resolve_which(name: str, *, cwd: Path | str | None = None) -> str | None:
    """Resolve an executable the way the host would.

    Also looks in the project's ``.venv`` / ``node_modules/.bin`` so
    ``pytest`` / ``uv`` / ``ruff`` work without a global install.
    """
    n = (name or "").strip()
    if not n:
        return None
    key = n.lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if key.endswith(".exe"):
        key = key[:-4]
    from remedy.core.computer import host_binding
    from remedy.core.computer.host_binding import HostError
    from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

    try:
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
                from remedy.core.build_python import is_usable_host_python

                if not is_usable_host_python(hit):
                    hit = ""
            if hit:
                return hit
    except HostError:
        raise
    except (NativeRuntimeUnavailableError, OSError):
        pass
    if cwd is not None:
        try:
            from remedy.core.project_fingerprint import local_bin_dirs

            suffix = ".exe" if os.name == "nt" else ""
            for bin_dir in local_bin_dirs(cwd):
                cand = bin_dir / (n + suffix if suffix and not n.lower().endswith(suffix) else n)
                if cand.is_file():
                    return str(cand)
                if suffix:
                    alt = bin_dir / f"{key}{suffix}"
                    if alt.is_file():
                        return str(alt)
        except OSError:
            pass
    def _ok_python(path: str | None) -> bool:
        if not path:
            return False
        from remedy.core.build_python import is_usable_host_python

        return is_usable_host_python(path)

    found = shutil.which(n)
    if found and (key not in {"python", "python3", "py"} or _ok_python(found)):
        return found
    if os.name == "nt" and not n.lower().endswith(".exe"):
        found = shutil.which(n + ".exe")
        if found and (key not in {"python", "python3", "py"} or _ok_python(found)):
            return found
    if key in {"python", "python3"}:
        # Frozen Desktop: ``sys.executable`` is the sidecar, which would print
        # its own usage and exit 2. Ask for a real interpreter instead —
        # host_python_executable resolves ['py', '-3'] to the concrete
        # python.exe rather than truncating the launcher argv to bare ``py``.
        try:
            from remedy.core.build_python import host_python_executable

            hit = host_python_executable()
        except OSError:
            hit = ""
        if hit and _ok_python(hit):
            return hit
        from remedy.core.runtime_identity import is_frozen_install

        if is_frozen_install():
            return None
        exe = sys.executable or ""
        return exe if _ok_python(exe) else None
    return None


def default_script_lang(home: str | Path | None = None) -> str:
    """pwsh when this PC has it; otherwise python (POSIX) or cmd."""
    if os.name != "nt":
        return "python"
    from remedy.core.computer import host_binding
    from remedy.core.computer.host_binding import HostError
    from remedy.runtime.native_runtime import NativeRuntimeUnavailableError

    try:
        d = host_binding.dialect_load(str(home) if home else "")
        pwsh = str(d.get("pwsh_cmd") or "").strip()
        if pwsh and Path(pwsh).is_file():
            return "pwsh"
    except HostError:
        raise
    except (NativeRuntimeUnavailableError, OSError):
        pass
    if shutil.which("pwsh") or shutil.which("powershell"):
        return "pwsh"
    return "cmd"


# --- Scratch script launch (Zig write/argv; Python cleanup + frozen python) ---

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
