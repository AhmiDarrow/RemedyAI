"""Prepare a host command: classify → translate → argv or script-file."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remedy.execution.host.ir import HostOp
from remedy.execution.host.scriptfile import (
    extract_powershell_payload,
    is_encoded_powershell,
    launch_script,
)
from remedy.execution.host.translate import looks_like_powershell, translate_posix_to_host
from remedy.execution.process import win_shell_prefix

# Cmd builtins that still need a shell after translation.
_CMD_BUILTINS = frozenset(
    {
        "echo",
        "cd",
        "dir",
        "type",
        "copy",
        "move",
        "del",
        "erase",
        "md",
        "mkdir",
        "rd",
        "rmdir",
        "set",
        "setlocal",
        "endlocal",
        "if",
        "for",
        "call",
        "exit",
        "rem",
        "ver",
        "cls",
        "color",
        "title",
        "pushd",
        "popd",
        "shift",
        "pause",
        "assoc",
        "ftype",
        "start",
        "vol",
        "date",
        "time",
        "path",
        "prompt",
        "where",
        "mklink",
        "xcopy",
    }
)

_SHELL_META = re.compile(r"[|<>&^%()]|&&|\|\|")


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


def looks_like_plain_argv(command: str) -> bool:
    """True when *command* is a single native process + args (no shell)."""
    cmd = (command or "").strip()
    if not cmd or _SHELL_META.search(cmd):
        return False
    if looks_like_powershell(cmd):
        return False
    try:
        toks = shlex.split(cmd, posix=os.name != "nt")
    except ValueError:
        return False
    if not toks:
        return False
    head = toks[0].lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if head.endswith(".exe"):
        head = head[:-4]
    return head not in _CMD_BUILTINS


def prepare_host_command(
    command: str,
    *,
    scratch_dir: Path | None = None,
    project_path: str | Path | None = None,
    host: str | None = None,
) -> PreparedCommand:
    """Turn a model-emitted command string into an argv the sandbox can exec."""
    raw = (command or "").strip()
    resolved_host = host or ("cmd" if os.name == "nt" else "posix")
    if not raw:
        return PreparedCommand(
            argv=[*win_shell_prefix(), ""],
            display="",
            kind="raw",
            ir=HostOp(kind="raw", text=""),
            host=resolved_host,
        )

    # Encoded PowerShell stays raw so the write jail sees the original bytes.
    if is_encoded_powershell(raw):
        return PreparedCommand(
            argv=[*win_shell_prefix(), raw],
            display=raw,
            kind="raw",
            ir=HostOp(kind="raw", text=raw, host=resolved_host),
            notes=["encoded powershell left raw for jail"],
            host=resolved_host,
        )

    if looks_like_powershell(raw) or _is_ps_wrapper(raw):
        body = extract_powershell_payload(raw) or raw
        launch = launch_script(
            "pwsh", body, scratch_dir=scratch_dir, project_path=project_path
        )
        return PreparedCommand(
            argv=launch.argv,
            display=f"pwsh -File {launch.path}",
            kind="script",
            ir=HostOp(kind="script", lang="pwsh", body=body),
            script_path=launch.path,
            notes=["powershell → temp .ps1 + pwsh -File"],
            host="pwsh",
        )

    tr = translate_posix_to_host(raw, host="cmd" if resolved_host != "posix" else "posix")
    text = tr.text
    notes = list(tr.notes)
    if tr.untranslatable:
        raise ValueError(
            "untranslatable substitution $(…) / backticks / ${} — use host_script"
        )
    if tr.noop:
        return PreparedCommand(
            argv=[],
            display=raw,
            kind="noop",
            ir=HostOp(kind="raw", text=raw, host=resolved_host),
            notes=notes or ["chmod ignored on Windows host"],
            host=resolved_host,
        )

    if resolved_host != "posix" and looks_like_plain_argv(text):
        argv = coerce_argv(text)
        if argv:
            resolved = resolve_which(argv[0])
            if resolved:
                argv[0] = resolved
            notes.append("plain argv — no shell")
            return PreparedCommand(
                argv=argv,
                display=" ".join(argv),
                kind="argv",
                ir=HostOp(kind="run", argv=argv),
                notes=notes,
                translated=text if text != raw else "",
                host=resolved_host,
            )

    argv = [*win_shell_prefix(), text]
    kind = "translated" if tr.changed else "raw"
    return PreparedCommand(
        argv=argv,
        display=text,
        kind=kind,
        ir=HostOp(kind="raw", text=text, host=resolved_host),
        notes=notes,
        translated=text if text != raw else "",
        host=resolved_host,
    )


def prepare_host_op(
    op: HostOp,
    *,
    scratch_dir: Path | None = None,
    project_path: str | Path | None = None,
) -> PreparedCommand:
    """Prepare argv from a structured HostOp (no command-string parsing)."""
    if op.kind == "run":
        argv = [str(a) for a in op.argv if str(a)]
        return PreparedCommand(
            argv=argv,
            display=" ".join(argv),
            kind="argv",
            ir=op,
            host=op.host or ("cmd" if os.name == "nt" else "posix"),
        )
    if op.kind == "script":
        launch = launch_script(
            op.lang or "pwsh",
            op.body,
            scratch_dir=scratch_dir,
            project_path=project_path,
        )
        return PreparedCommand(
            argv=launch.argv,
            display=f"{launch.lang} -File {launch.path}",
            kind="script",
            ir=op,
            script_path=launch.path,
            host=launch.lang,
        )
    if op.kind == "raw":
        return prepare_host_command(
            op.text,
            scratch_dir=scratch_dir,
            project_path=project_path,
            host=op.host or None,
        )
    # mkdir / which / env are executed without a shell by the tools themselves
    return PreparedCommand(
        argv=[],
        display=op.kind,
        kind=op.kind,
        ir=op,
        host=op.host or ("cmd" if os.name == "nt" else "posix"),
    )


def resolve_which(name: str) -> str | None:
    """Resolve an executable the way the host would."""
    n = (name or "").strip()
    if not n:
        return None
    key = n.lower().rsplit("\\", 1)[-1].rsplit("/", 1)[-1]
    if key.endswith(".exe"):
        key = key[:-4]
    try:
        from remedy.execution.host.dialect import load_dialect

        d = load_dialect()
        mapped = {
            "python": d.python_cmd,
            "python3": d.python_cmd,
            "py": d.python_cmd,
            "git": d.git_cmd,
            "rg": d.rg_cmd,
            "pwsh": d.pwsh_cmd,
        }
        hit = (mapped.get(key) or "").strip()
        if hit and Path(hit).is_file():
            return hit
    except Exception:
        pass
    found = shutil.which(n)
    if found:
        return found
    if os.name == "nt" and not n.lower().endswith(".exe"):
        found = shutil.which(n + ".exe")
        if found:
            return found
    if key in {"python", "python3"}:
        return sys.executable
    return None


def default_script_lang(home: str | Path | None = None) -> str:
    """pwsh when this PC has it; otherwise python (POSIX) or cmd."""
    if os.name != "nt":
        return "python"
    try:
        from remedy.execution.host.dialect import load_dialect

        d = load_dialect(home)
        if (d.pwsh_cmd or "").strip() and Path(d.pwsh_cmd).is_file():
            return "pwsh"
    except Exception:
        pass
    if shutil.which("pwsh") or shutil.which("powershell"):
        return "pwsh"
    return "cmd"


def _is_ps_wrapper(command: str) -> bool:
    return bool(
        re.match(
            r"(?is)^\s*(?:.*\\)?(?:powershell|pwsh)(?:\.exe)?\b",
            command or "",
        )
    )
