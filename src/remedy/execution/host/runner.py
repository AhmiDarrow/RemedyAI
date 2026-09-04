"""Prepare a host command: classify → translate → argv or script-file."""

from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from remedy.execution.host.ir import HostOp
from remedy.execution.host.translate import looks_like_powershell

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

_SHELL_META_CHARS = set("|<>&^%()")


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


# Modules `uv run <name>` should exec as `python -m <name>` so the Windows
# desktop (a GUI process) does not flash a CMD for uv's python child.
_UV_RUN_MODULES = frozenset(
    {
        "pytest",
        "ruff",
        "mypy",
        "pip",
        "httpx",
        "uvicorn",
        "http.server",
    }
)


def deflate_uv_run(
    argv: list[str], *, project_path: Path | str | None = None
) -> list[str]:
    """Turn ``uv run pytest`` into ``python -m pytest``.

    CREATE_NO_WINDOW hides *uv.exe*, but uv then CreateProcess's python.exe
    without that flag. The desktop sidecar has no console, so that python
    child opens a visible CMD for every test/lint. Exec the project
    interpreter ourselves and the flag sticks.
    """
    if len(argv) < 3:
        return argv
    head = argv[0].replace("\\", "/").rsplit("/", 1)[-1].lower()
    if head.endswith(".exe"):
        head = head[:-4]
    if head != "uv" or str(argv[1]).lower() != "run":
        return argv
    rest = list(argv[2:])
    while rest and str(rest[0]).startswith("-"):
        flag = str(rest[0]).lower()
        if flag in {"--directory", "--project", "-p", "--package"} and len(rest) > 1:
            rest = rest[2:]
            continue
        rest = rest[1:]
    if not rest:
        return argv
    py = resolve_which("python", cwd=project_path)
    if not py:
        return argv
    tool = str(rest[0]).replace("\\", "/").rsplit("/", 1)[-1].lower()
    if tool.endswith(".exe"):
        tool = tool[:-4]
    if tool in {"python", "python3", "py"}:
        return [py, *rest[1:]]
    if tool in _UV_RUN_MODULES:
        return [py, "-m", tool, *rest[1:]]
    if tool.endswith(".py"):
        return [py, *rest]
    return argv


def _exe_stem(name: str) -> str:
    """``C:\\bin\\git.exe`` / ``git`` → ``git``."""
    head = str(name or "").replace("\\", "/").rsplit("/", 1)[-1].lower()
    return head[:-4] if head.endswith(".exe") else head


def _unquote_cmd_token(tok: str) -> str:
    t = (tok or "").strip()
    if len(t) >= 2 and t[0] == '"' and t[-1] == '"':
        return t[1:-1].replace('""', '"')
    return t


def _argv_for_hidden_hop(part: str) -> list[str]:
    """Argv for one ``&&`` hop. Strip cmd.exe's leftover quotes on Windows."""
    hop = coerce_argv(part)
    if os.name != "nt":
        return hop
    return [_unquote_cmd_token(tok) for tok in hop]


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


_IF_MKDIR = re.compile(
    r"(?is)^\(\s*if\s+not\s+exist\s+(?:\"[^\"]*\"|\S+)\s+mkdir\s+(\"[^\"]*\"|\S+)\s*\)$"
)


def split_and_segments(text: str) -> list[str] | None:
    """Quote-aware ``&&`` split. None if fewer than two hops or quotes never close."""
    raw = (text or "").strip()
    if not raw or "||" in raw:
        return None
    parts: list[str] = []
    buf: list[str] = []
    quote = ""
    i = 0
    while i < len(raw):
        ch = raw[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            buf.append(ch)
            i += 1
            continue
        if raw.startswith("&&", i):
            parts.append("".join(buf).strip())
            buf = []
            i += 2
            continue
        buf.append(ch)
        i += 1
    if quote:
        return None
    parts.append("".join(buf).strip())
    parts = [p for p in parts if p]
    return parts if len(parts) >= 2 else None


def split_plain_and_chain(text: str) -> list[str] | None:
    """Split ``A && B && C`` into segments when every hop is a plain argv.

    Quote-aware so ``git commit -m "a && b"`` stays one hop. Pipes, ``||``,
    redirects, parens, and cmd builtins still need a real shell.
    """
    parts = split_and_segments(text)
    if not parts or any(not looks_like_plain_argv(p) for p in parts):
        return None
    return parts


def _shell_chain_text(argv: list[str]) -> str | None:
    if len(argv) < 3:
        return None
    head = _exe_stem(argv[0])
    flag = str(argv[1]).lower()
    if head == "cmd" and flag == "/c":
        return str(argv[2]) if len(argv) == 3 else " ".join(str(a) for a in argv[2:])
    if head in {"sh", "bash"} and flag == "-c":
        return str(argv[2]) if len(argv) == 3 else " ".join(str(a) for a in argv[2:])
    return None


def _parse_cd_hop(text: str) -> str | None:
    toks = _argv_for_hidden_hop(text)
    if not toks or _exe_stem(toks[0]) != "cd":
        return None
    rest = list(toks[1:])
    if rest and rest[0].lower() == "/d":
        rest = rest[1:]
    if len(rest) != 1:
        return None
    return rest[0]


def _parse_one_mkdir(text: str) -> list[str] | None:
    t = (text or "").strip()
    matched = _IF_MKDIR.match(t)
    if matched:
        return [_unquote_cmd_token(matched.group(1))]
    toks = _argv_for_hidden_hop(t)
    if not toks or _exe_stem(toks[0]) not in {"mkdir", "md"}:
        return None
    paths = [p for p in toks[1:] if p not in {"-p", "--parents"} and not p.startswith("-")]
    return paths or None


def _parse_mkdir_hop(text: str) -> list[str] | None:
    t = (text or "").strip()
    if " & " in t and "&&" not in t:
        paths: list[str] = []
        for part in t.split(" & "):
            one = _parse_one_mkdir(part.strip())
            if not one:
                return None
            paths.extend(one)
        return paths or None
    return _parse_one_mkdir(t)


def classify_chain_hop(
    text: str,
    *,
    project_path: Path | str | None = None,
) -> ChainHop | None:
    """Map one ``&&`` segment to cd / mkdir / a hidden argv. None = needs a shell."""
    cd = _parse_cd_hop(text)
    if cd is not None:
        return ChainHop.cd(cd)
    mk = _parse_mkdir_hop(text)
    if mk:
        return ChainHop.mkdir(mk)
    if not looks_like_plain_argv(text):
        return None
    hop = _argv_for_hidden_hop(text)
    if not hop:
        return None
    if not Path(hop[0]).is_file():
        resolved = resolve_which(hop[0], cwd=project_path)
        if resolved:
            hop[0] = resolved
    return ChainHop.run(deflate_uv_run(hop, project_path=project_path))


def expand_shell_chain(
    argv: list[str],
    *,
    project_path: Path | str | None = None,
) -> list[ChainHop] | None:
    """Turn ``cmd /c A && B`` into hidden cd/mkdir/run hops."""
    text = _shell_chain_text(argv)
    if not text:
        return None
    parts = split_and_segments(text)
    if not parts:
        return None
    hops: list[ChainHop] = []
    for part in parts:
        hop = classify_chain_hop(part, project_path=project_path)
        if hop is None:
            return None
        hops.append(hop)
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


def _unquoted_has_shell_meta(cmd: str) -> bool:
    """True if shell metacharacters appear outside quotes (or quotes never close)."""
    quote = ""
    i = 0
    n = len(cmd)
    while i < n:
        ch = cmd[i]
        if quote:
            if ch == quote:
                quote = ""
            i += 1
            continue
        if ch in "\"'":
            quote = ch
            i += 1
            continue
        if ch in _SHELL_META_CHARS:
            return True
        if cmd.startswith("&&", i) or cmd.startswith("||", i):
            return True
        i += 1
    return bool(quote)


def looks_like_plain_argv(command: str) -> bool:
    """True when *command* is a single native process + args (no shell)."""
    cmd = (command or "").strip()
    if not cmd or _unquoted_has_shell_meta(cmd):
        return False
    if looks_like_powershell(cmd):
        return False
    try:
        toks = shlex.split(cmd, posix=os.name != "nt")
    except ValueError:
        return False
    if not toks:
        return False
    return _exe_stem(toks[0]) not in _CMD_BUILTINS


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
    (Zig ABI 4). ConPTY and policy remain Python.
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
            if key in {"python", "python3", "py"}:
                from remedy.core.build_python import is_usable_host_python

                if not is_usable_host_python(hit):
                    hit = ""
            if hit:
                return hit
    except Exception:
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
        except Exception:
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
        except Exception:
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
