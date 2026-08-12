"""Workspace tools — shell."""

from __future__ import annotations

from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.errors import format_tool_error
from remedy.core.react_policy import (
    HARD_SAFETY_CHARS as _HARD_SAFETY_CHARS,
)
from remedy.core.security import check_dangerous_command
from remedy.core.workspace_tools.guards import (
    junk_write_guard,
    note_path,
    parent_hint,
    reserved_guard,
    track_read,
)


def _spawn_background(
    argv: list[str],
    *,
    cwd: Path,
    env: dict[str, str] | None,
    command: str,
    auto: bool = False,
) -> str:
    """Start a command and return immediately (GUI / server / game)."""
    import os
    import subprocess

    kwargs: dict[str, Any] = {
        "cwd": str(cwd) if cwd else None,
        "env": env,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "stdin": subprocess.DEVNULL,
        "close_fds": True,
    }
    if os.name == "nt":
        # Visible console + GUI. DETACHED_PROCESS hides many pygame/SDL windows.
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_CONSOLE", 0x00000010)
            | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)
        )
    else:
        kwargs["start_new_session"] = True
    try:
        proc = subprocess.Popen(argv, **kwargs)
    except OSError as e:
        return format_tool_error(
            f"failed to start background command: {e}",
            code="SPAWN_FAILED",
            tool_name="bash_exec",
            suggestion="Check the path exists and is executable.",
        )
    note = (
        " (auto: looks like a GUI/game — not waiting for exit)"
        if auto
        else ""
    )
    return (
        f"started background pid={proc.pid} cwd={cwd}{note}\n"
        f"command={command}\n"
        "The process is running. Use computer_app or computer_snapshot "
        "target=desktop to play/inspect the window. Do not treat this as "
        "exit_code=0 of a finished program — observe the UI next."
    )


def _normalize_shell_command_for_host(command: str) -> str:
    """Rewrite model-emitted bashisms for the host shell (esp. Windows cmd).

    Models often generate ``mkdir -p a/b c/d`` and ``cd X && mkdir -p …`` which
    break under PowerShell/cmd without -p. Prefer file_write for files; shell
    still needs mkdir for dirs when the model insists.
    """
    import os
    import re

    cmd = (command or "").strip()
    if not cmd:
        return cmd
    if os.name != "nt":
        return cmd

    def _mkdir_p_replacement(paths_blob: str) -> str:
        # Split on whitespace but keep quoted segments
        parts: list[str] = []
        for m in re.finditer(r'"[^"]+"|\'[^\']+\'|\S+', paths_blob.strip()):
            p = m.group(0).strip().strip("\"'")
            if not p or p.startswith("-"):
                continue
            # Trailing \ required so IF NOT EXIST treats path as a directory
            win_p = p.replace("/", "\\").rstrip("\\")
            parts.append(f'if not exist "{win_p}\\" mkdir "{win_p}"')
        return " & ".join(parts) if parts else "echo no_paths"

    # Global replace of `mkdir -p PATH…` segments (including after &&)
    def _sub_mkdir(m: re.Match[str]) -> str:
        return _mkdir_p_replacement(m.group(1))

    cmd2 = re.sub(
        r"\bmkdir\s+-p\s+((?:\"[^\"]+\"|'[^']+'|[^\s&|;]+(?:\s+(?!&&)[^\s&|;]+)*)+)",
        _sub_mkdir,
        cmd,
        flags=re.IGNORECASE,
    )
    # `cd /d` is fine on cmd; bare unix `cd path &&` already works on cmd
    # Normalize forward slashes in cd targets when simple: leave as-is (cmd accepts /)
    return cmd2


def register_shell_tools(runtime: Any) -> None:
    """Register shell workspace tools."""
    def _parent_hint(path: str) -> str:
        return parent_hint(path)

    def _reserved_guard(path: str) -> str | None:
        return reserved_guard(path)

    def _junk_write_guard(path: str) -> str | None:
        return junk_write_guard(path)

    def _note_path(target: Path) -> None:
        note_path(runtime, target)

    def _track_read(target: Path) -> None:
        track_read(runtime, target)

    async def bash_exec(
        command: str = "",
        timeout_seconds: float = 180.0,
        workdir: str = "",
        description: str = "",
        background: bool = False,
    ) -> str:
        """Run a shell command through SubprocessSandbox (hidden console on Windows).

        *timeout_seconds* default 180, clamped to 5–600. *workdir* optional
        absolute or relative path (defaults to focus/default cwd). Local
        venv/node_modules/.bin and repo-root tools are prepended to PATH.
        *description* is accepted (and ignored) when models pass a human note.
        *background*: return immediately after spawn (games / servers). GUI
        launches are auto-backgrounded so the turn can play the window.
        """
        _ = description
        from remedy.core.approvals import APPROVALS
        from remedy.core.project_fingerprint import path_env_with_local_bins
        from remedy.execution.process import win_shell_prefix
        from remedy.execution.sandbox import SubprocessSandbox

        if not command or not str(command).strip():
            return format_tool_error(
                "empty command",
                code="EMPTY_COMMAND",
                tool_name="bash_exec",
                suggestion="Pass a non-empty shell command string.",
            )
        danger = check_dangerous_command(["bash", "-c", command])
        if danger:
            suggestion = (
                "Use a safer equivalent (read files with file_read/list_dir; "
                "avoid destructive or network-restricted commands)."
            )
            low = danger.lower()
            if any(
                k in low
                for k in (
                    "app.exe",
                    "named app",
                    "tauri",
                    "remedy desktop",
                    "port 7400",
                    "remedy.exe",
                    "remedy serve",
                    "host agent",
                    "host api",
                )
            ):
                suggestion = (
                    "Self-preservation: do not kill bare app.exe / remedy / port 7400. "
                    "Restart a project UI only with a Path/CommandLine filter for that "
                    r'folder (e.g. Where-Object { $_.Path -match "SecretFolder" }).'
                )
            return format_tool_error(
                f"blocked by security policy: {danger}",
                code="SECURITY_BLOCK",
                tool_name="bash_exec",
                suggestion=suggestion,
            )
        # Partner trust: bash_exec always asks in ask-mode (high-impact tool)
        from remedy.core.turn_context import turn_session_id

        ask_reason = APPROVALS.needs_ask(command, tool_name="bash_exec")
        sid = turn_session_id(runtime)
        if ask_reason and not APPROVALS.is_approved(
            "bash_exec", command, session_id=sid
        ):
            item = APPROVALS.create(
                tool_name="bash_exec",
                command=command,
                reason=ask_reason,
                session_id=sid,
            )
            return (
                f"APPROVAL_REQUIRED id={item.id}\n"
                f"reason={ask_reason}\n"
                f"command={command[:400]}\n"
                "Do not invent success. Tell the user this needs approval in the UI "
                f"(or /approve {item.id}). After they approve, retry bash_exec with "
                "the same command."
            )
        root = runtime.effective_project_path()
        cwd = root
        wd_raw = (workdir or "").strip()
        if wd_raw:
            bad_wd = _reserved_guard(wd_raw)
            if bad_wd:
                return format_tool_error(
                    bad_wd,
                    code="RESERVED_NAME",
                    tool_name="bash_exec",
                    suggestion="Use a normal directory for workdir.",
                )
            try:
                # Shell cwd is a mutation surface — jail to write roots.
                cwd = runtime.resolve_tool_path(wd_raw, for_write=True)
                if cwd.is_file():
                    cwd = cwd.parent
            except Exception as e:
                return format_tool_error(
                    f"invalid workdir: {e}",
                    code="BAD_WORKDIR",
                    tool_name="bash_exec",
                    suggestion=(
                        "Pass a workdir under the project folder (project scope). "
                        "Raise access_scope to home/full for shell outside the project."
                    ),
                )
        try:
            timeout = float(timeout_seconds if timeout_seconds is not None else 180.0)
        except (TypeError, ValueError):
            timeout = 180.0
        timeout = max(5.0, min(600.0, timeout))

        # Write roots only — never fall back to read roots (Desktop/Docs/Downloads).
        try:
            roots = list(runtime.write_roots() or [])
        except Exception as exc:
            return format_tool_error(
                f"cannot resolve write roots for shell jail: {exc}",
                code="WRITE_JAIL",
                tool_name="bash_exec",
                suggestion=(
                    "Ensure a project folder is set. Shell refused fail-closed "
                    "(will not fall back to profile read roots)."
                ),
            )
        if not roots:
            roots = [root]

        # Project write jail for shell mutations (not just cwd). Fail closed.
        from remedy.core.shell_write_jail import check_shell_write_jail

        try:
            bound = not bool(runtime.project_path_is_unset())
        except Exception:
            bound = True  # fail closed: treat as bound if unknown
        try:
            scope = str(runtime.access_scope() or "project")
        except Exception:
            scope = "project"
        try:
            jail_hit = check_shell_write_jail(
                command,
                write_roots=list(roots),
                cwd=cwd,
                project_bound=bound,
                access_scope=scope,
            )
        except Exception as exc:
            return format_tool_error(
                f"shell write jail check failed (refused): {exc}",
                code="WRITE_JAIL",
                tool_name="bash_exec",
                suggestion=(
                    "Stay inside the focus project with file_write/file_edit. "
                    "Jail check errors fail closed — shell not executed."
                ),
            )
        if jail_hit:
            return format_tool_error(
                jail_hit,
                code="WRITE_JAIL",
                tool_name="bash_exec",
                suggestion=(
                    "Stay inside the focus project. Prefer file_write/file_edit. "
                    "Do not retarget sibling folders (SecretFolder vs SecretSticky). "
                    "To edit another tree, switch session project explicitly with the user."
                ),
            )

        # Translate common bashisms so Windows cmd can run model commands
        command = _normalize_shell_command_for_host(command)
        argv = [*win_shell_prefix(), command]
        sandbox = SubprocessSandbox(allowed_paths=roots or [root, cwd])
        env = path_env_with_local_bins(cwd)

        auto_bg = False
        if not background:
            with suppress(Exception):
                from remedy.core.interactive_launch import command_looks_like_gui_launch

                auto_bg = command_looks_like_gui_launch(command, cwd)
        if background or auto_bg:
            return _spawn_background(
                argv,
                cwd=cwd,
                env=env,
                command=command,
                auto=auto_bg and not background,
            )

        result = await sandbox.execute(
            argv, workdir=cwd, timeout_seconds=timeout, env=env
        )
        parts = [f"exit_code={result.exit_code}", f"cwd={cwd}", f"timeout_s={timeout}"]
        # Full stdout/stderr — no quality truncation for the model.
        if result.stdout:
            out = result.stdout
            if len(out) > _HARD_SAFETY_CHARS:
                out = out[:_HARD_SAFETY_CHARS] + f"\n…[stdout safety cap {_HARD_SAFETY_CHARS}]"
            parts.append(out)
        if result.stderr:
            err = result.stderr
            if len(err) > _HARD_SAFETY_CHARS:
                err = err[:_HARD_SAFETY_CHARS] + f"\n…[stderr safety cap {_HARD_SAFETY_CHARS}]"
            parts.append(f"stderr:\n{err}")
        if result.exit_code != 0:
            parts.append(
                "Suggestion: Read stderr, fix flags/paths/cwd, raise timeout_seconds "
                "for long builds, or try a different command; use list_dir/file_read "
                "if you only need file contents."
            )
            # Best-effort path:line extraction for faster fix loops
            with suppress(Exception):
                import re as _re

                blob = (result.stderr or "") + "\n" + (result.stdout or "")
                locs: list[str] = []
                for m in _re.finditer(
                    r"([A-Za-z]:\\[^\s:\"']+\.\w{1,8}|[^\s:\"']+\.\w{1,8}):(\d+)",
                    blob,
                ):
                    loc = f"{m.group(1)}:{m.group(2)}"
                    if loc not in locs:
                        locs.append(loc)
                    if len(locs) >= 5:
                        break
                if locs:
                    parts.append(
                        "Likely locations (file_read these):\n"
                        + "\n".join(f"- {x}" for x in locs)
                    )
        return "\n".join(parts)

    async def run_python_file(
        path: str = "",
        args: str = "",
        timeout_seconds: float = 120.0,
        workdir: str = "",
    ) -> str:
        """Run a .py file with the project Python (prefer over python -c blobs).

        *path* relative to project or absolute. Optional *args* is a single
        argv string split with shlex. Temp helpers should live under
        ``.remedy-build/tmp/``.
        """
        import os
        import shlex
        import sys
        from pathlib import Path as _P

        from remedy.core.project_fingerprint import path_env_with_local_bins
        from remedy.execution.sandbox import SubprocessSandbox

        rel = (path or "").strip()
        if not rel:
            return format_tool_error(
                "path= required",
                code="EMPTY_PATH",
                tool_name="run_python_file",
                suggestion="Pass path to a .py file (prefer .remedy-build/tmp/ for helpers).",
            )
        bad = _reserved_guard(rel)
        if bad:
            return format_tool_error(
                bad,
                code="RESERVED_NAME",
                tool_name="run_python_file",
                suggestion="Use a normal .py path under the project.",
            )
        try:
            target = runtime.resolve_tool_path(rel, for_write=False)
        except Exception as e:
            return format_tool_error(
                f"invalid path: {e}",
                code="BAD_PATH",
                tool_name="run_python_file",
                suggestion="Use a path under the project folder.",
            )
        if not target.is_file():
            return format_tool_error(
                f"not a file: {target}",
                code="NOT_FOUND",
                tool_name="run_python_file",
                suggestion="file_write the script first (prefer .remedy-build/tmp/).",
            )
        if target.suffix.lower() not in (".py", ".pyw"):
            return format_tool_error(
                f"not a Python file: {target.name}",
                code="NOT_PYTHON",
                tool_name="run_python_file",
                suggestion="Pass a .py path, or use bash_exec for other runners.",
            )
        gui_py = False
        with suppress(Exception):
            from remedy.core.interactive_launch import path_looks_like_gui

            gui_py = path_looks_like_gui(target)
        root = runtime.effective_project_path()
        cwd = root
        wd_raw = (workdir or "").strip()
        if wd_raw:
            try:
                cwd = runtime.resolve_tool_path(wd_raw, for_write=True)
                if cwd.is_file():
                    cwd = cwd.parent
            except Exception as e:
                return format_tool_error(
                    f"invalid workdir: {e}",
                    code="BAD_WORKDIR",
                    tool_name="run_python_file",
                    suggestion="Pass workdir under the project.",
                )
        else:
            # Default cwd = project root (not script dir) for package imports
            try:
                cwd = _P(root) if root else target.parent
                if cwd.is_file():
                    cwd = cwd.parent
            except Exception:
                cwd = target.parent
        try:
            timeout = float(timeout_seconds if timeout_seconds is not None else 120.0)
        except (TypeError, ValueError):
            timeout = 120.0
        timeout = max(5.0, min(600.0, timeout))
        extra: list[str] = []
        if (args or "").strip():
            try:
                extra = shlex.split(args, posix=os.name != "nt")
            except Exception:
                extra = args.split()

        argv = [sys.executable, str(target), *extra]
        if gui_py:
            env_bg = path_env_with_local_bins(cwd)
            return _spawn_background(
                argv,
                cwd=cwd,
                env=env_bg,
                command=" ".join(argv),
                auto=True,
            )
        try:
            roots = list(runtime.write_roots() or [])
        except Exception:
            roots = [root]
        sandbox = SubprocessSandbox(allowed_paths=roots or [root, cwd])
        env = path_env_with_local_bins(cwd)
        result = await sandbox.execute(
            argv, workdir=cwd, timeout_seconds=timeout, env=env
        )
        parts = [
            f"exit_code={result.exit_code}",
            f"cwd={cwd}",
            f"python={sys.executable}",
            f"script={target}",
            f"timeout_s={timeout}",
        ]
        if result.stdout:
            out = result.stdout
            if len(out) > _HARD_SAFETY_CHARS:
                out = out[:_HARD_SAFETY_CHARS] + f"\n…[stdout safety cap {_HARD_SAFETY_CHARS}]"
            parts.append(out)
        if result.stderr:
            err = result.stderr
            if len(err) > _HARD_SAFETY_CHARS:
                err = err[:_HARD_SAFETY_CHARS] + f"\n…[stderr safety cap {_HARD_SAFETY_CHARS}]"
            parts.append(f"stderr:\n{err}")
        if result.exit_code != 0:
            parts.append(
                "Suggestion: fix the script with file_edit, or raise timeout_seconds. "
                "Prefer .remedy-build/tmp/ for one-off helpers (not repo root)."
            )
        return "\n".join(parts)

    runtime.tool_registry.register_builtin_handler(
        "bash_exec",
        "Run a shell command. Default cwd = focus folder (or home). "
        "Optional workdir= absolute/relative path; timeout_seconds= 5–600 (default 180). "
        "background=true returns immediately (games/servers). GUI/game launches "
        "auto-background so you can computer_snapshot the window. "
        "Local .venv/node_modules/.bin and repo-root tools are on PATH. "
        "Prefer run_python_file for .py scripts; prefer file_write over echo redirects. "
        "Temp helper scripts → .remedy-build/tmp/ only.",
        bash_exec,
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
                "timeout_seconds": {
                    "type": "number",
                    "description": "Timeout seconds (default 180, max 600)",
                    "default": 180,
                },
                "workdir": {
                    "type": "string",
                    "description": "Optional working directory (absolute or relative)",
                },
                "background": {
                    "type": "boolean",
                    "description": (
                        "If true, spawn and return immediately (pid). "
                        "Use for games, GUIs, and long servers."
                    ),
                },
                "description": {
                    "type": "string",
                    "description": (
                        "Optional human note about the command (ignored by the runner; "
                        "accepted so models do not fail on extra kwargs)"
                    ),
                },
            },
            "required": ["command"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "run_python_file",
        "Run a .py file with project Python (prefer over python -c). "
        "path= required; args= optional argv string; workdir= optional. "
        "Temp helpers should live under .remedy-build/tmp/.",
        run_python_file,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Path to .py file"},
                "args": {
                    "type": "string",
                    "description": "Optional arguments string (shlex-split)",
                },
                "timeout_seconds": {
                    "type": "number",
                    "description": "Timeout seconds (default 120, max 600)",
                    "default": 120,
                },
                "workdir": {
                    "type": "string",
                    "description": "Optional working directory",
                },
            },
            "required": ["path"],
        },
    )

