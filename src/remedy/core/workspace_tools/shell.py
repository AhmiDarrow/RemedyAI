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


def _join_argv_for_jail(argv: list[str]) -> str:
    """Join argv for jail/approval text without losing spaces."""
    bits: list[str] = []
    for a in argv:
        s = str(a)
        if not s:
            continue
        if any(ch.isspace() for ch in s) or any(ch in s for ch in '&|<>^'):
            bits.append('"' + s.replace('"', '""') + '"')
        else:
            bits.append(s)
    return " ".join(bits)


def _normalize_shell_command_for_host(command: str) -> str:
    """Rewrite model-emitted bashisms for the host shell (Windows cmd).

    Thin wrapper over Host Bridge translate — kept so older call sites and
    tests that import this symbol still work.
    """
    from remedy.execution.host.translate import translate_posix_to_host

    return translate_posix_to_host(command).text


async def _run_host_session(
    *,
    command: str,
    cwd: Path,
    timeout: float,
    env: dict[str, str] | None,
    roots: list[Path],
    use_conpty: bool = False,
    host: str | None = None,
) -> str:
    """Run *command* in the shared persistent host session (opt-in)."""
    from remedy.core.errors import format_tool_error
    from remedy.execution.host.diagnose import diagnose_host_failure
    from remedy.execution.host.session import get_shared_session

    try:
        sess = await get_shared_session(
            host=host, cwd=str(cwd), env=env, use_conpty=use_conpty
        )
        result = await sess.run(command, timeout=timeout)
    except Exception as exc:
        return format_tool_error(
            f"host session failed: {exc}",
            code="HOST_SESSION",
            tool_name="bash_exec",
            suggestion="Retry without session=true, or use host_run(argv).",
        )
    # After cd, refuse if the session walked outside write roots.
    if result.cwd:
        try:
            here = Path(result.cwd).expanduser().resolve(strict=False)
            allowed = False
            for r in roots:
                try:
                    root = Path(r).expanduser().resolve(strict=False)
                    if here == root or here.is_relative_to(root):
                        allowed = True
                        break
                except (OSError, ValueError, TypeError):
                    continue
            if not allowed:
                return format_tool_error(
                    f"host session cwd left write roots: {result.cwd}",
                    code="WRITE_JAIL",
                    tool_name="bash_exec",
                    suggestion="cd back into the project, or close the session.",
                )
        except OSError:
            pass
    parts = [
        f"exit_code={result.exit_code}",
        f"cwd={result.cwd or cwd}",
        f"timeout_s={timeout}",
        f"host=session:{result.host}",
    ]
    if result.used_conpty:
        parts.append("conpty=1")
    if result.stdout:
        out = result.stdout
        if len(out) > _HARD_SAFETY_CHARS:
            out = out[:_HARD_SAFETY_CHARS] + f"\n…[stdout safety cap {_HARD_SAFETY_CHARS}]"
        parts.append(out)
    if result.exit_code != 0 or result.timed_out:
        diag = diagnose_host_failure(
            command,
            stdout=result.stdout or "",
            exit_code=result.exit_code,
            timed_out=result.timed_out or result.interactive,
            host=result.host,
        )
        parts.append(diag.format_block())
    return "\n".join(parts)


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
        session: bool = False,
        conpty: bool = False,
        _argv: list[str] | None = None,
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
        with suppress(Exception):
            from remedy.core.self_inject_draft import (
                in_internal_improve,
                internal_improve_shell_ok,
            )

            if in_internal_improve() and not internal_improve_shell_ok(command):
                return format_tool_error(
                    "Unattended self-fix may only run pytest / ruff / py_compile.",
                    code="WRITE_JAIL",
                    tool_name="bash_exec",
                    suggestion="Do not git push, publish, or run arbitrary shell.",
                )
        import re as _re

        from remedy.core.shell_write_jail import (
            extract_path_candidates,
            scan_script_source_for_outside_writes,
        )

        py_tokens = [
            t
            for t in extract_path_candidates(command)
            if str(t).lower().endswith(".py")
        ]
        if not py_tokens:
            py_tokens = [
                m.group(0)
                for m in _re.finditer(r"(?:\.?[/\\])?[\w./\\-]+\.py\b", command, _re.I)
            ]
        for tok in py_tokens:
            try:
                cand = Path(tok)
                if not cand.is_absolute():
                    cand = Path(cwd) / tok
                if cand.is_file():
                    src_hit = scan_script_source_for_outside_writes(
                        cand, write_roots=list(roots)
                    )
                    if src_hit:
                        return format_tool_error(
                            src_hit,
                            code="WRITE_JAIL",
                            tool_name="bash_exec",
                            suggestion="Keep launched script I/O under the project folder.",
                        )
            except Exception:
                continue

        from remedy.execution.host.diagnose import diagnose_host_failure
        from remedy.execution.host.ir import HostOp
        from remedy.execution.host.runner import PreparedCommand, prepare_host_command

        try:
            if _argv:
                prepared = PreparedCommand(
                    argv=list(_argv),
                    display=" ".join(str(a) for a in _argv),
                    kind="argv",
                    ir=HostOp(kind="run", argv=list(_argv)),
                    notes=["host_run argv"],
                )
            else:
                prepared = prepare_host_command(command, project_path=root)
        except ValueError as exc:
            return format_tool_error(
                str(exc),
                code="HOST_PREPARE",
                tool_name="bash_exec",
                suggestion="Shrink the script or use file_write + run_python_file.",
            )
        argv = prepared.argv
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
                command=prepared.display or command,
                auto=auto_bg and not background,
            )

        if session:
            sess_cmd = (
                _join_argv_for_jail(list(_argv))
                if _argv
                else (prepared.display or command)
            )
            return await _run_host_session(
                command=sess_cmd,
                cwd=cwd,
                timeout=timeout,
                env=env,
                roots=list(roots),
                use_conpty=bool(conpty),
            )

        result = await sandbox.execute(
            argv, workdir=cwd, timeout_seconds=timeout, env=env
        )
        parts = [
            f"exit_code={result.exit_code}",
            f"cwd={cwd}",
            f"timeout_s={timeout}",
            f"host={prepared.kind}",
        ]
        if prepared.notes:
            parts.append("host_notes: " + "; ".join(prepared.notes[:6]))
        if prepared.script_path:
            parts.append(f"script={prepared.script_path}")
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
        timed_out = result.exit_code == -1 and "timed out" in (result.stderr or "").lower()
        if result.exit_code != 0:
            diag = diagnose_host_failure(
                command,
                stdout=result.stdout or "",
                stderr=result.stderr or "",
                exit_code=result.exit_code,
                translated=prepared.translated or prepared.display,
                timed_out=timed_out,
                host=prepared.host,
            )
            parts.append(diag.format_block())
            parts.append(
                "Suggestion: Prefer host_run(argv=[...]) / host_mkdir / host_script. "
                "Read stderr, fix flags/paths/cwd, or raise timeout_seconds."
            )
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
        else:
            with suppress(Exception):
                from remedy.execution.host.dialect import record_success

                record_success(command, note=prepared.kind)
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
            target = runtime.resolve_tool_path(rel, for_write=True)
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
        try:
            roots = list(runtime.write_roots() or [])
        except Exception:
            roots = [cwd]
        if not roots:
            roots = [cwd]
        try:
            bound = not bool(runtime.project_path_is_unset())
        except Exception:
            bound = True
        try:
            scope = str(runtime.access_scope() or "project")
        except Exception:
            scope = "project"
        from remedy.core.shell_write_jail import check_shell_write_jail

        try:
            jail_hit = check_shell_write_jail(
                " ".join(str(a) for a in argv),
                write_roots=list(roots),
                cwd=cwd,
                project_bound=bound,
                access_scope=scope,
            )
        except Exception as exc:
            return format_tool_error(
                f"shell write jail check failed (refused): {exc}",
                code="WRITE_JAIL",
                tool_name="run_python_file",
                suggestion="Keep scripts under the project folder.",
            )
        if jail_hit:
            return format_tool_error(
                jail_hit,
                code="WRITE_JAIL",
                tool_name="run_python_file",
                suggestion="Write and run scripts under the project folder.",
            )
        from remedy.core.shell_write_jail import scan_script_source_for_outside_writes

        src_hit = scan_script_source_for_outside_writes(target, write_roots=list(roots))
        if src_hit:
            return format_tool_error(
                src_hit,
                code="WRITE_JAIL",
                tool_name="run_python_file",
                suggestion="Keep script I/O under the project folder.",
            )
        if gui_py:
            env_bg = path_env_with_local_bins(cwd)
            return _spawn_background(
                argv,
                cwd=cwd,
                env=env_bg,
                command=" ".join(argv),
                auto=True,
            )
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
        "Run a host command (Windows = cmd.exe, not bash). POSIX-ish strings "
        "(mkdir -p, rm -rf, grep) are rewritten; PowerShell is written to a "
        "temp .ps1 and run with pwsh -File (never -Command). "
        "Prefer host_run(argv) / host_mkdir / host_script / file_write. "
        "Default cwd = focus folder. timeout_seconds= 5–600 (default 180). "
        "background=true for games/servers. session=true keeps cwd/env. "
        "Temp scripts → .remedy-build/tmp/ only.",
        bash_exec,
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Host command to run"},
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
                "session": {
                    "type": "boolean",
                    "description": (
                        "If true, run in the persistent host session (cwd/env survive)."
                    ),
                },
                "conpty": {
                    "type": "boolean",
                    "description": "Attach a Windows ConPTY when session=true (TTY apps).",
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

    async def host_run(
        argv: Any = None,
        timeout_seconds: float = 180.0,
        workdir: str = "",
        session: bool = False,
        conpty: bool = False,
    ) -> str:
        """Run a native argv (no shell). Accepts list or string."""
        from remedy.execution.host.runner import coerce_argv

        args = coerce_argv(argv)
        if not args:
            return format_tool_error(
                "argv= required (list or string)",
                code="EMPTY_COMMAND",
                tool_name="host_run",
                suggestion='Pass argv=["python","-m","py_compile","app.py"].',
            )
        # Jail/approval see a quoted string; execution uses the raw argv list.
        return await bash_exec(
            command=_join_argv_for_jail(args),
            timeout_seconds=timeout_seconds,
            workdir=workdir,
            session=session,
            conpty=conpty,
            _argv=args,
        )

    async def host_mkdir(paths: Any = None, workdir: str = "") -> str:
        """Create directories (parents=True) under write roots. No shell."""
        from remedy.execution.host.runner import coerce_argv

        items = coerce_argv(paths)
        if not items:
            return format_tool_error(
                "paths= required",
                code="EMPTY_PATH",
                tool_name="host_mkdir",
                suggestion='Pass paths=["src","tests"] or a single path string.',
            )
        made: list[str] = []
        for raw in items:
            bad = _reserved_guard(raw)
            if bad:
                return format_tool_error(
                    bad, code="RESERVED_NAME", tool_name="host_mkdir"
                )
            try:
                target = runtime.resolve_tool_path(raw, for_write=True)
            except Exception as e:
                return format_tool_error(
                    f"invalid path: {e}",
                    code="BAD_PATH",
                    tool_name="host_mkdir",
                    suggestion="Use a path under the project folder.",
                )
            try:
                target.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                return format_tool_error(
                    f"mkdir failed: {e}",
                    code="MKDIR_FAILED",
                    tool_name="host_mkdir",
                )
            _note_path(target)
            made.append(str(target))
        return "ok\n" + "\n".join(made)

    async def host_which(name: str = "") -> str:
        """Resolve an executable on PATH (and common Windows names)."""
        from remedy.execution.host.runner import resolve_which

        n = (name or "").strip()
        if not n:
            return format_tool_error(
                "name= required",
                code="EMPTY_NAME",
                tool_name="host_which",
                suggestion="Pass name=python or name=git.",
            )
        found = resolve_which(n)
        if not found:
            return format_tool_error(
                f"not on PATH: {n}",
                code="NOT_FOUND",
                tool_name="host_which",
                suggestion="Install the tool or pass a full path to host_run.",
            )
        return found

    async def host_script(
        lang: str = "pwsh",
        body: str = "",
        timeout_seconds: float = 180.0,
        workdir: str = "",
    ) -> str:
        """Write a scratch script and run it with -File (never -Command)."""
        from remedy.execution.host.scriptfile import launch_script

        text = (body or "").strip()
        if not text:
            return format_tool_error(
                "body= required",
                code="EMPTY_COMMAND",
                tool_name="host_script",
                suggestion="Pass the script body; it is written under .remedy-build/tmp/.",
            )
        kind = (lang or "pwsh").strip().lower() or "pwsh"
        if kind in ("powershell", "ps1"):
            kind = "pwsh"
        if kind not in ("pwsh", "cmd", "python"):
            return format_tool_error(
                f"unsupported lang={kind}",
                code="BAD_LANG",
                tool_name="host_script",
                suggestion="lang=pwsh | cmd | python.",
            )
        if kind == "python":
            root = runtime.effective_project_path()
            try:
                launch = launch_script("python", text, project_path=root)
            except ValueError as exc:
                return format_tool_error(
                    str(exc),
                    code="HOST_PREPARE",
                    tool_name="host_script",
                    suggestion="Shrink the script body.",
                )
            return await run_python_file(
                path=str(launch.path),
                timeout_seconds=timeout_seconds,
                workdir=workdir,
            )
        # Jail the body, then let Host Bridge write .ps1/.cmd and run -File.
        prefix = "pwsh -Command " if kind == "pwsh" else "cmd /c "
        return await bash_exec(
            command=prefix + text,
            timeout_seconds=timeout_seconds,
            workdir=workdir,
        )

    runtime.tool_registry.register_builtin_handler(
        "host_run",
        "Run a native process by argv (no shell, no quoting). "
        'argv=["git","status"] or a string. Prefer this over bash_exec.',
        host_run,
        {
            "type": "object",
            "properties": {
                "argv": {
                    "description": "Argument vector (array or string)",
                    "anyOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string"},
                    ],
                },
                "timeout_seconds": {"type": "number", "default": 180},
                "workdir": {"type": "string"},
                "session": {"type": "boolean"},
                "conpty": {"type": "boolean"},
            },
            "required": ["argv"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "host_mkdir",
        "Create directories under write roots (parents=True). No shell. "
        'paths=["src/foo","tests"] or a single path.',
        host_mkdir,
        {
            "type": "object",
            "properties": {
                "paths": {
                    "description": "Directories to create",
                    "anyOf": [
                        {"type": "array", "items": {"type": "string"}},
                        {"type": "string"},
                    ],
                },
                "workdir": {"type": "string"},
            },
            "required": ["paths"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "host_which",
        "Resolve an executable on this machine's PATH (python, git, rg, …).",
        host_which,
        {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": ["name"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "host_script",
        "Write a scratch script under .remedy-build/tmp/ and run it "
        "(pwsh -File / cmd / python). Never use powershell -Command.",
        host_script,
        {
            "type": "object",
            "properties": {
                "lang": {
                    "type": "string",
                    "enum": ["pwsh", "cmd", "python"],
                    "default": "pwsh",
                },
                "body": {"type": "string", "description": "Script source"},
                "timeout_seconds": {"type": "number", "default": 180},
                "workdir": {"type": "string"},
            },
            "required": ["body"],
        },
    )

