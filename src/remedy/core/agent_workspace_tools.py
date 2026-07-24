"""Workspace file/shell tool registration (extracted from BasicRuntime)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from remedy.core.errors import format_tool_error
from remedy.core.react_policy import (
    FILE_READ_CHAR_CAP as _FILE_READ_CHAR_CAP,
)
from remedy.core.react_policy import (
    HARD_SAFETY_CHARS as _HARD_SAFETY_CHARS,
)
from remedy.core.security import check_dangerous_command


def register_workspace_tools(runtime: Any) -> None:
    """Register file/shell tools jailed to the project workspace."""

    def _parent_hint(path: str) -> str:
        p = (path or ".").strip() or "."
        if p in (".", "./", ""):
            return "."
        parent = Path(p).parent.as_posix()
        return parent if parent not in ("", ".") else "."

    async def file_read(path: str = ".") -> str:
        runtime.effective_project_path()
        target = runtime.resolve_tool_path(path)
        if not target.exists():
            parent = _parent_hint(path)
            return format_tool_error(
                f"file not found: {path}",
                code="NOT_FOUND",
                tool_name="file_read",
                suggestion=(
                    f"Call list_dir on '{parent}' or project root ('.') "
                    "to discover the correct path, then retry file_read."
                ),
            )
        if target.is_dir():
            return format_tool_error(
                f"path is a directory: {path}",
                code="IS_DIRECTORY",
                tool_name="file_read",
                suggestion=(
                    f'Use list_dir("{path}") then file_read on a specific file inside it.'
                ),
            )
        try:
            data = target.read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            return format_tool_error(
                f"cannot read {path}: {e}",
                code="IO_ERROR",
                tool_name="file_read",
                suggestion="Check permissions or try list_dir on the parent path.",
            )
        # Full file contents — only emergency OOM guard (not a quality limit).
        cap = _FILE_READ_CHAR_CAP if _FILE_READ_CHAR_CAP > 0 else _HARD_SAFETY_CHARS
        if len(data) > cap:
            return (
                data[:cap]
                + f"\n\n... [safety cap {cap} chars of {len(data)} — "
                f"read a smaller path or a specific section if needed]"
            )
        runtime._track_artifact(str(target))
        return data

    async def file_write(path: str, content: str = "") -> str:
        from remedy.core.approvals import APPROVALS

        ask_reason = APPROVALS.needs_ask(f"write {path}", tool_name="file_write")
        sid = getattr(runtime, "_session_id", None)
        if ask_reason and not APPROVALS.is_approved(
            "file_write", f"write {path}", session_id=sid
        ):
            item = APPROVALS.create(
                tool_name="file_write",
                command=f"write {path}",
                reason=ask_reason,
                session_id=sid,
            )
            return (
                f"APPROVAL_REQUIRED id={item.id}\n"
                f"reason={ask_reason}\n"
                f"path={path}\n"
                "Do not invent success. Tell the user this needs approval in the UI "
                f"(or /approve {item.id}). After they approve, retry file_write."
            )
        target = runtime.resolve_tool_path(path)
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        except OSError as e:
            parent = _parent_hint(path)
            return format_tool_error(
                f"cannot write {path}: {e}",
                code="IO_ERROR",
                tool_name="file_write",
                suggestion=(
                    f"Verify the parent path with list_dir('{parent}') "
                    "and ensure the path is inside allowed roots "
                    f"(access scope: {runtime.access_scope()})."
                ),
            )
        runtime._track_artifact(str(target))
        return f"Wrote {len(content)} bytes to {path}"

    async def list_dir(path: str = ".") -> str:
        root = runtime.effective_project_path()
        target = runtime.resolve_tool_path(path)
        if not target.exists():
            parent = _parent_hint(path)
            return format_tool_error(
                f"path not found: {path}",
                code="NOT_FOUND",
                tool_name="list_dir",
                suggestion=(
                    f"Call list_dir on '{parent}' or project root ('.') "
                    "to find the correct directory name."
                ),
            )
        if not target.is_dir():
            return format_tool_error(
                f"not a directory: {path}",
                code="NOT_A_DIRECTORY",
                tool_name="list_dir",
                suggestion=f'Use file_read("{path}") for file contents instead.',
            )
        lines: list[str] = []
        try:
            for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
                if p.name.startswith("."):
                    continue
                try:
                    rel = p.relative_to(root).as_posix()
                except ValueError:
                    rel = str(p)
                lines.append(f"{'dir ' if p.is_dir() else 'file'} {rel}")
                # Generous listing (no short 200-entry wall).
                if len(lines) >= 50_000:
                    lines.append(f"... ({len(lines)}+ entries; listing safety stop)")
                    break
        except OSError as e:
            return format_tool_error(
                f"cannot list {path}: {e}",
                code="IO_ERROR",
                tool_name="list_dir",
                suggestion="Retry with project root '.' or a known subdirectory.",
            )
        return "\n".join(lines) if lines else "(empty)"

    async def bash_exec(command: str = "") -> str:
        """Run a shell command through SubprocessSandbox (hidden console on Windows)."""
        from remedy.core.approvals import APPROVALS
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
            return format_tool_error(
                f"blocked by security policy: {danger}",
                code="SECURITY_BLOCK",
                tool_name="bash_exec",
                suggestion=(
                    "Use a safer equivalent (read files with file_read/list_dir; "
                    "avoid destructive or network-restricted commands)."
                ),
            )
        # Partner trust: bash_exec always asks in ask-mode (high-impact tool)
        ask_reason = APPROVALS.needs_ask(command, tool_name="bash_exec")
        sid = getattr(runtime, "_session_id", None)
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
        roots = runtime.allowed_roots()
        argv = [*win_shell_prefix(), command]
        sandbox = SubprocessSandbox(allowed_paths=roots or [root])
        result = await sandbox.execute(argv, workdir=root, timeout_seconds=60.0)
        parts = [f"exit_code={result.exit_code}", f"cwd={root}"]
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
                "Suggestion: Read stderr, fix flags/paths/cwd, or try a different "
                "command; use list_dir/file_read if you only need file contents."
            )
        return "\n".join(parts)

    runtime.tool_registry.register_builtin_handler(
        "file_read",
        "Read a text file under allowed roots (see access scope). "
        "Prefer paths relative to the project root.",
        file_read,
        {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Relative path within the project"},
            },
            "required": ["path"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "file_write",
        "Create or overwrite a text file (UTF-8). Preferred for all simple "
        "create/edit of .txt/.md/.json — do NOT use bash/powershell Set-Content. "
        "Allowed: project, Desktop, Documents, Downloads (plus home when access "
        "scope is home/full). Absolute Desktop paths are fine.",
        file_write,
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Absolute or project-relative path",
                },
                "content": {
                    "type": "string",
                    "description": "Full file contents to write",
                },
            },
            "required": ["path", "content"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "list_dir",
        "List files and directories under allowed roots (see access scope).",
        list_dir,
        {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Relative directory (default: project root)",
                },
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "bash_exec",
        "Run a shell command (cwd = project). Do NOT use for simple text file "
        "create/edit — use file_write instead (avoids PowerShell quoting failures).",
        bash_exec,
        {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "Shell command to run"},
            },
            "required": ["command"],
        },
    )
    runtime._register_comfyui_tools()
    runtime._register_vision_tools()
    runtime._register_local_discover_tools()
    runtime._register_skill_tools()
    # Per-turn tool trace for auto-learn (reset each stream_response)
    runtime._turn_tool_steps: list[dict[str, Any]] = []
    runtime._learning_loop = None  # lazy LearningLoop

