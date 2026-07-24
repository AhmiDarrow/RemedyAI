"""Concrete agent runtime -- BasicRuntime with LLM integration and ReAct tool use.

Provides the default Remedy agent: a multi-step ReAct loop that stores conversation
in memory, calls LLM providers through the adapter layer, and invokes tools
through the ToolRegistry.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp

from remedy.core.errors import SecurityError, format_tool_error
from remedy.core.providers import ProviderAdapter, get_provider
from remedy.core.react_policy import (
    HISTORY_CHAR_BUDGET as _HISTORY_CHAR_BUDGET,
    HISTORY_MSG_LIMIT as _HISTORY_MSG_LIMIT,
    MAX_PARALLEL_TOOLS as _MAX_PARALLEL_TOOLS,
    MAX_REACT_STEPS as _MAX_REACT_STEPS,
    _build_system_prompt,
    _looks_like_pseudo_tools,
    _message_wants_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
    batch_has_tool_errors,
    message_wants_tools,
    recovery_nudge_message,
    tool_content_is_error,
)
from remedy.core.react_stream import (
    StreamRoundState,
    apply_openai_sse_chunk,
    build_assistant_api_message,
    build_runtime_system_block,
    ensure_tool_call_pairings,
    filter_fresh_tool_calls,
    finalize_round_text,
    normalize_tool_calls,
    parse_sse_data_line,
    repair_reasoning_content_in_messages,
    should_enable_tools,
)
from remedy.core.runtime import AgentRuntime
from remedy.core.security import check_dangerous_command
from remedy.core.workspace import (
    allowed_roots_for_scope,
    ensure_project_dir,
    normalize_access_scope,
    resolve_project_path,
    resolve_under_roots,
    workspace_context_block,
)
from remedy.memory.store import MemoryStore
from remedy.models import (
    AgentConfig,
    ChatMessageRole,
    GatewayEvent,
    ToolCall,
    ToolResult,
)
from remedy.skills.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# Re-export for tests that import from remedy.core.agent
__all__ = [
    "BasicRuntime",
    "_message_wants_tools",
    "message_wants_tools",
]


class BasicRuntime(AgentRuntime):
    """Default concrete agent runtime with LLM integration and tool support.

    Features:
    - Processes gateway events with conversation memory
    - Multi-provider LLM integration via provider adapters
    - Multi-step ReAct tool loop when tools are registered
    - Streaming and non-streaming response modes
    - Falls back to echo-style responses when no LLM is configured
    """

    def __init__(self, config: AgentConfig, memory: MemoryStore | None = None) -> None:
        super().__init__(config, memory=memory)
        self.tool_registry = ToolRegistry()
        self._system_prompt = _build_system_prompt(getattr(config, "persona", None))
        self._llm_api_key: str = config.llm_api_key
        self._llm_model: str = config.llm_model
        self._llm_base_url: str = config.llm_base_url or "https://api.openai.com/v1"
        self._llm_provider: str = getattr(config, "llm_provider", "openai") or "openai"
        self._provider: ProviderAdapter = get_provider(self._llm_provider)
        self._max_react_steps = _MAX_REACT_STEPS
        # Default workspace from config; per-session override applied in stream_response.
        self._default_project_path: Path = resolve_project_path(
            getattr(config, "project_path", None)
        )
        self._active_project_path: Path = self._default_project_path
        self._access_scope: str = normalize_access_scope(
            getattr(config, "access_scope", None) or "project"
        )
        self._harness_mode: str = (
            str(getattr(config, "harness_mode", None) or "auto").strip().lower()
        )
        self._harness_min_pct: float = float(
            getattr(config, "harness_min_context_pct", None) or 0.35
        )
        self._harness_max_pct: float = float(
            getattr(config, "harness_max_context_pct", None) or 0.70
        )
        # Memory Harness L2 working state (per agent instance / session)
        self._session_brief = None  # type: ignore[assignment]
        self._register_workspace_tools()
        self._register_memory_tools()

    def effective_project_path(self) -> Path:
        """Active workspace root for tools / context (session or default)."""
        try:
            return ensure_project_dir(self._active_project_path)
        except Exception:
            return resolve_project_path(None)

    def access_scope(self) -> str:
        return normalize_access_scope(self._access_scope)

    def allowed_roots(self) -> list[Path]:
        return allowed_roots_for_scope(
            self.access_scope(), self.effective_project_path()
        )

    def resolve_tool_path(self, path: str) -> Path:
        """Resolve a tool path under the current access scope roots."""
        return resolve_under_roots(
            path or ".",
            self.allowed_roots(),
            access_scope=self.access_scope(),
        )

    def set_project_path(self, path: str | Path | None, *, as_default: bool = False) -> Path:
        """Set active (and optionally default) project workspace."""
        resolved = resolve_project_path(
            str(path) if path is not None else None,
            fallback=self._default_project_path,
        )
        try:
            resolved = ensure_project_dir(resolved)
        except Exception:
            pass
        self._active_project_path = resolved
        if as_default:
            self._default_project_path = resolved
            if hasattr(self, "config") and self.config is not None:
                try:
                    self.config.project_path = str(resolved)
                except Exception:
                    pass
        return resolved

    def _register_workspace_tools(self) -> None:
        """Register file/shell tools jailed to the project workspace."""

        def _parent_hint(path: str) -> str:
            p = (path or ".").strip() or "."
            if p in (".", "./", ""):
                return "."
            parent = Path(p).parent.as_posix()
            return parent if parent not in ("", ".") else "."

        async def file_read(path: str = ".") -> str:
            root = self.effective_project_path()
            target = self.resolve_tool_path(path)
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
            # Cap large files for context safety
            if len(data) > 200_000:
                return data[:200_000] + f"\n\n... [truncated, {len(data)} bytes total]"
            self._track_artifact(str(target))
            return data

        async def file_write(path: str, content: str = "") -> str:
            target = self.resolve_tool_path(path)
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
                        f"(access scope: {self.access_scope()})."
                    ),
                )
            self._track_artifact(str(target))
            return f"Wrote {len(content)} bytes to {path}"

        async def list_dir(path: str = ".") -> str:
            root = self.effective_project_path()
            target = self.resolve_tool_path(path)
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
                    if len(lines) >= 200:
                        lines.append("... (truncated)")
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
            # Partner trust: high-impact patterns require explicit user approval first
            ask_reason = APPROVALS.needs_ask(command)
            sid = getattr(self, "_session_id", None)
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
            root = self.effective_project_path()
            roots = self.allowed_roots()
            argv = [*win_shell_prefix(), command]
            sandbox = SubprocessSandbox(allowed_paths=roots or [root])
            result = await sandbox.execute(argv, workdir=root, timeout_seconds=60.0)
            parts = [f"exit_code={result.exit_code}", f"cwd={root}"]
            if result.stdout:
                parts.append(result.stdout[:50_000])
            if result.stderr:
                parts.append(f"stderr:\n{result.stderr[:20_000]}")
            if result.exit_code != 0:
                parts.append(
                    "Suggestion: Read stderr, fix flags/paths/cwd, or try a different "
                    "command; use list_dir/file_read if you only need file contents."
                )
            return "\n".join(parts)

        self.tool_registry.register_builtin_handler(
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
        self.tool_registry.register_builtin_handler(
            "file_write",
            "Write a text file under allowed roots (see access scope).",
            file_write,
            {
                "type": "object",
                "properties": {
                    "path": {"type": "string"},
                    "content": {"type": "string"},
                },
                "required": ["path", "content"],
            },
        )
        self.tool_registry.register_builtin_handler(
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
        self.tool_registry.register_builtin_handler(
            "bash_exec",
            "Run a shell command with cwd set to the project working directory.",
            bash_exec,
            {
                "type": "object",
                "properties": {
                    "command": {"type": "string", "description": "Shell command to run"},
                },
                "required": ["command"],
            },
        )

    def _register_memory_tools(self) -> None:
        """Memory + Memory Harness tools (search, save, compress)."""

        async def memory_search(query: str = "", limit: int = 8) -> str:
            if self.memory is None:
                return "Memory store not available."
            q = (query or "").strip()
            if not q:
                return "Provide a search query."
            try:
                hits = await self.memory.search(q, limit=max(1, min(int(limit), 20)))
            except Exception as e:
                return f"Memory search failed: {e}"
            if not hits:
                return f"No memory matches for: {q}"
            lines = []
            for e in hits:
                title = getattr(e, "title", "") or ""
                content = (getattr(e, "content", None) or "")[:200]
                lines.append(f"- {title}: {content}" if title else f"- {content}")
            return "Memory hits:\n" + "\n".join(lines)

        async def memory_save(
            content: str = "",
            title: str = "Remembered",
            category: str = "general",
        ) -> str:
            if self.memory is None:
                return "Memory store not available."
            text = (content or "").strip()
            if not text:
                return "Nothing to save — provide content."
            try:
                from remedy.models import MemoryEntry, MemoryEntryType

                await self.memory.upsert(
                    MemoryEntry(
                        title=(title or "Remembered")[:120],
                        content=text,
                        entry_type=MemoryEntryType.NOTE,
                        importance=0.75,
                    )
                )
                # Also surface as a user fact when short
                if len(text) < 400:
                    with suppress(Exception):
                        profile = await self.memory.get_or_create_profile()
                        profile.add_fact(
                            text, category=category or "general", confidence=0.85
                        )
                        await self.memory.save_user_profile(profile)
                return f"Saved to memory: {(title or 'Remembered')[:80]}"
            except Exception as e:
                return f"Memory save failed: {e}"

        async def compress_context(focus: str = "") -> str:
            """Memory Harness L1: merge history into Session Brief (send-view stays lean)."""
            from remedy.memory.harness.brief import SessionBrief
            from remedy.memory.harness.compressor import heuristic_merge_from_history

            if self._session_brief is None:
                self._session_brief = SessionBrief(
                    session_id=getattr(self, "_session_id", None) or ""
                )
            history: list[dict[str, Any]] = []
            sid = getattr(self, "_session_id", None)
            if sid and self.memory is not None:
                with suppress(Exception):
                    history = await self._load_session_history(sid, "")
            self._session_brief = heuristic_merge_from_history(
                self._session_brief,
                history,
                intent_hint=(focus or None),
            )
            brief = self._session_brief
            return (
                f"Memory Harness compressed (pass #{brief.compress_count}). "
                f"Intent: {brief.intent or '(set)'}. "
                f"Artifacts: {len(brief.artifacts)}. "
                f"Decisions: {len(brief.decisions)}."
            )

        self.tool_registry.register_builtin_handler(
            "memory_search",
            "Search durable Remedy memory for relevant notes and facts.",
            memory_search,
            {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "description": "Max results (default 8)"},
                },
                "required": ["query"],
            },
        )
        self.tool_registry.register_builtin_handler(
            "memory_save",
            "Save a durable note or fact about the user or project to memory.",
            memory_save,
            {
                "type": "object",
                "properties": {
                    "content": {"type": "string"},
                    "title": {"type": "string"},
                    "category": {"type": "string", "description": "e.g. work, personal, preference"},
                },
                "required": ["content"],
            },
        )
        self.tool_registry.register_builtin_handler(
            "compress_context",
            "Memory Harness: compress stale session detail into the Session Brief "
            "(intent, files, decisions, next steps). Call when a subtask finishes or context is large.",
            compress_context,
            {
                "type": "object",
                "properties": {
                    "focus": {
                        "type": "string",
                        "description": "Optional focus for what to keep in the brief",
                    },
                },
            },
        )

        # --- Partner loop: goals ---
        async def goal_add(title: str = "", description: str = "") -> str:
            t = (title or "").strip()
            if not t:
                return "Provide a goal title."
            task = self.create_task(t, description=description or "", tags=["goal"])
            with suppress(Exception):
                from remedy.memory.harness.brief import SessionBrief

                if self._session_brief is None:
                    self._session_brief = SessionBrief()
                if t not in self._session_brief.open_tasks:
                    self._session_brief.open_tasks.append(t)
                    self._session_brief.open_tasks = self._session_brief.open_tasks[-20:]
                    self._session_brief.touch()
            return f"Goal added id={task.id} title={t}"

        async def goal_list(status: str = "") -> str:
            from remedy.models import TaskStatus

            st = (status or "").strip().lower()
            tasks = self.list_tasks()
            if st:
                try:
                    enum_st = TaskStatus(st)
                    tasks = [t for t in tasks if t.status == enum_st]
                except Exception:
                    tasks = [t for t in tasks if t.status.value == st]
            tagged = [t for t in tasks if "goal" in (t.tags or [])]
            use = tagged if tagged else list(tasks)
            if not use:
                return "No goals yet. Use goal_add to create one."
            lines = []
            for t in use[:30]:
                lines.append(
                    f"- [{t.status.value}] {t.title}"
                    + (f" — {t.result_summary}" if t.result_summary else "")
                    + f"  (id={t.id})"
                )
            return "Goals:\n" + "\n".join(lines)

        async def goal_complete(title: str = "", evidence: str = "") -> str:
            from datetime import UTC, datetime

            from remedy.models import TaskStatus

            needle = (title or "").strip().lower()
            if not needle:
                return "Provide goal title (or partial) to complete."
            matches = [
                t
                for t in self.list_tasks()
                if needle in t.title.lower() and t.status != TaskStatus.COMPLETED
            ]
            if not matches:
                return f"No open goal matching: {title}"
            task = matches[0]
            task.status = TaskStatus.COMPLETED
            task.result_summary = (evidence or "done").strip()[:500]
            task.completed_at = datetime.now(UTC)
            task.updated_at = datetime.now(UTC)
            with suppress(Exception):
                if self._session_brief is not None:
                    self._session_brief.open_tasks = [
                        x
                        for x in self._session_brief.open_tasks
                        if x.lower() != task.title.lower()
                    ]
                    if evidence:
                        self._session_brief.decisions.append(
                            f"Completed goal: {task.title} — {evidence[:120]}"
                        )
                        self._session_brief.decisions = self._session_brief.decisions[-20:]
                    self._session_brief.touch()
            # Learn: store short success note
            if self.memory is not None and evidence:
                with suppress(Exception):
                    from remedy.models import MemoryEntry, MemoryEntryType

                    await self.memory.upsert(
                        MemoryEntry(
                            title=f"Goal done: {task.title}",
                            content=evidence[:2000],
                            entry_type=MemoryEntryType.NOTE,
                            tags=["goal", "verified"],
                            importance=0.7,
                        )
                    )
            return f"Goal completed: {task.title}" + (
                f" evidence={evidence[:200]}" if evidence else ""
            )

        async def goal_verify(title: str = "", evidence: str = "") -> str:
            """Record verification evidence for a goal (partner verify loop)."""
            if not (evidence or "").strip():
                return "Provide evidence of completion (command output, file path, result)."
            # Completing with evidence is the verify path
            return await goal_complete(title=title, evidence=evidence)

        self.tool_registry.register_builtin_handler(
            "goal_add",
            "Add a user goal / checklist item for this session (partner loop).",
            goal_add,
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "description": {"type": "string"},
                },
                "required": ["title"],
            },
        )
        self.tool_registry.register_builtin_handler(
            "goal_list",
            "List tracked goals and their status.",
            goal_list,
            {
                "type": "object",
                "properties": {
                    "status": {
                        "type": "string",
                        "description": "Optional filter: created, in_progress, completed",
                    },
                },
            },
        )
        self.tool_registry.register_builtin_handler(
            "goal_complete",
            "Mark a goal complete; optionally store evidence for the verify/learn loop.",
            goal_complete,
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["title"],
            },
        )
        self.tool_registry.register_builtin_handler(
            "goal_verify",
            "Verify a goal with evidence (path, test output, screenshot note) and mark done.",
            goal_verify,
            {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "evidence": {"type": "string"},
                },
                "required": ["title", "evidence"],
            },
        )

    def _track_artifact(self, path: str) -> None:
        """Record a path in the Session Brief (Memory Harness L2)."""
        with suppress(Exception):
            from remedy.memory.harness.brief import SessionBrief

            if self._session_brief is None:
                self._session_brief = SessionBrief()
            self._session_brief.add_artifact(path)

    def reconfigure_llm(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
        persona: str | None = None,
        name: str | None = None,
        project_path: str | None = None,
        access_scope: str | None = None,
        harness_mode: str | None = None,
        harness_min_context_pct: float | None = None,
        harness_max_context_pct: float | None = None,
    ) -> None:
        """Hot-apply LLM / persona / project settings without restarting."""
        if access_scope is not None:
            self._access_scope = normalize_access_scope(access_scope)
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    self.config.access_scope = self._access_scope
        if harness_mode is not None and str(harness_mode).strip():
            self._harness_mode = str(harness_mode).strip().lower()
        if harness_min_context_pct is not None:
            self._harness_min_pct = float(harness_min_context_pct)
        if harness_max_context_pct is not None:
            self._harness_max_pct = float(harness_max_context_pct)
        if provider is not None and provider.strip():
            self._llm_provider = provider.strip().lower()
            self._provider = get_provider(self._llm_provider)
            if hasattr(self, "config") and self.config is not None:
                try:
                    self.config.llm_provider = self._llm_provider
                except Exception:
                    pass
        if model is not None and model.strip():
            self._llm_model = model.strip()
            if hasattr(self, "config") and self.config is not None:
                try:
                    self.config.llm_model = self._llm_model
                except Exception:
                    pass
        if base_url is not None and base_url.strip():
            self._llm_base_url = base_url.strip()
            if hasattr(self, "config") and self.config is not None:
                try:
                    self.config.llm_base_url = self._llm_base_url
                except Exception:
                    pass
        if api_key is not None:
            # Empty string means leave unchanged (UI "keep current" path).
            if api_key != "":
                self._llm_api_key = api_key
                if hasattr(self, "config") and self.config is not None:
                    try:
                        self.config.llm_api_key = self._llm_api_key
                    except Exception:
                        pass
        if persona is not None:
            p = persona.strip().lower() if persona.strip() else "default"
            if hasattr(self, "config") and self.config is not None:
                try:
                    self.config.persona = p
                except Exception:
                    pass
            self._system_prompt = _build_system_prompt(p)
        if name is not None and name.strip():
            if hasattr(self, "config") and self.config is not None:
                try:
                    self.config.name = name.strip()
                except Exception:
                    pass
        if project_path is not None:
            # Allow clearing to cwd via empty string.
            self.set_project_path(project_path if project_path.strip() else None, as_default=True)

    async def handle_event(self, event: GatewayEvent) -> AsyncIterator[Any]:
        kind = event.kind.value if hasattr(event.kind, "value") else str(event.kind)

        if kind in ("heartbeat",):
            return

        yield f"[{self.config.name}] Processing {event.kind.value} from {event.channel.value}"

        message = event.payload.get("message", "")
        if not message:
            return

        if event.session_id:
            self._session_id = event.session_id

        await self.remember(
            content=f"User ({event.source_id}): {message}",
            title=f"Message from {event.source_id}",
            importance=0.5,
        )

        response = await self._generate_response(message, event)

        if response:
            await self.remember(
                content=f"Remedy: {response}",
                title="Agent response",
                importance=0.4,
            )
            yield response

    async def call_tool(self, tool_call: ToolCall) -> ToolResult:
        import time as _time

        from remedy.core.metrics import default_registry

        name = tool_call.tool_name
        default_registry.counter("remedy_tool_calls_total", tool=name).inc()
        t0 = _time.perf_counter()
        try:
            result = await self.tool_registry.execute(name, **tool_call.arguments)
            # Workspace tools often return Error-prefixed strings on soft failure;
            # still count as handler success, but surface metrics for recovery telemetry.
            if isinstance(result, str) and tool_content_is_error(result):
                default_registry.counter("remedy_tool_soft_errors_total", tool=name).inc()
            else:
                default_registry.counter("remedy_tool_success_total", tool=name).inc()
            default_registry.histogram(
                "remedy_tool_duration_seconds", tool=name
            ).observe(_time.perf_counter() - t0)
            return ToolResult(
                call_id=tool_call.id,
                success=True,
                data=result,
                duration_ms=(_time.perf_counter() - t0) * 1000,
            )
        except SecurityError as e:
            default_registry.counter("remedy_tool_errors_total", tool=name).inc()
            default_registry.histogram(
                "remedy_tool_duration_seconds", tool=name
            ).observe(_time.perf_counter() - t0)
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=format_tool_error(
                    str(e),
                    code="SECURITY_BLOCKED",
                    tool_name=name,
                    suggestion=(
                        "Stay inside the project workspace; use list_dir on the "
                        "project root and a relative path."
                    ),
                ),
                duration_ms=(_time.perf_counter() - t0) * 1000,
            )
        except ValueError as e:
            default_registry.counter("remedy_tool_errors_total", tool=name).inc()
            default_registry.histogram(
                "remedy_tool_duration_seconds", tool=name
            ).observe(_time.perf_counter() - t0)
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=format_tool_error(
                    str(e),
                    code="TOOL_VALUE_ERROR",
                    tool_name=name,
                    suggestion="Check tool arguments (path/command) and retry with corrected values.",
                ),
                duration_ms=(_time.perf_counter() - t0) * 1000,
            )
        except Exception as e:
            logger.exception("Tool %s failed", name)
            default_registry.counter("remedy_tool_errors_total", tool=name).inc()
            default_registry.histogram(
                "remedy_tool_duration_seconds", tool=name
            ).observe(_time.perf_counter() - t0)
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=format_tool_error(
                    str(e),
                    code="TOOL_EXCEPTION",
                    tool_name=name,
                    suggestion=(
                        "Try a different tool or args (list_dir / alternate path); "
                        "do not invent results."
                    ),
                ),
                duration_ms=(_time.perf_counter() - t0) * 1000,
            )

    async def _generate_response(
        self,
        message: str,
        event: GatewayEvent,
    ) -> str:
        if self._llm_api_key:
            return await self._call_llm(message)
        return (
            f"[FALLBACK MODE — No API key configured]\n\n"
            f"{self._fallback_response(message, event)}"
        )

    def _openai_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for t in self.tool_registry.tools:
            params = t.parameters if t.parameters else {"type": "object", "properties": {}}
            if "type" not in params:
                params = {"type": "object", "properties": params}
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or t.name,
                        "parameters": params,
                    },
                }
            )
        return tools

    async def _call_llm(self, message: str) -> str:
        """Call the LLM with ReAct tool-use loop (non-streaming)."""
        full = ""
        try:
            async for chunk in self._call_llm_stream(message, session_id=self._session_id):
                if not str(chunk).startswith("@@"):
                    full += chunk
            return full
        except Exception as e:
            logger.exception("LLM call failed")
            return f"\n[LLM EXCEPTION]\n{e}\n[END LLM EXCEPTION]"

    async def _load_session_history(
        self,
        session_id: str | None,
        current_user: str,
    ) -> list[dict[str, Any]]:
        """Load recent user/assistant turns for multi-turn continuity (OpenCode-style)."""
        if not session_id or self.memory is None:
            return []
        try:
            rows = await self.memory.get_chat_messages(
                session_id, limit=_HISTORY_MSG_LIMIT
            )
        except Exception:
            logger.debug("session history load failed", exc_info=True)
            return []

        # Drop trailing user message if API already persisted the current turn.
        if rows and rows[-1].role == ChatMessageRole.USER:
            last = (rows[-1].content or "").strip()
            if last == (current_user or "").strip():
                rows = rows[:-1]

        budget = _HISTORY_CHAR_BUDGET
        # Walk newest→oldest then reverse so we keep the most recent context.
        selected: list[dict[str, Any]] = []
        for msg in reversed(rows):
            role = msg.role.value if hasattr(msg.role, "value") else str(msg.role)
            if role not in ("user", "assistant"):
                continue
            content = (msg.content or "").strip()
            if not content:
                continue
            # Strip internal tool markers from prior assistant bubbles.
            if role == "assistant":
                if content.startswith("@@") or "[LLM" in content[:40]:
                    continue
                # Soft-trim huge prior answers (raised with HISTORY_CHAR_BUDGET)
                if len(content) > 12_000:
                    content = content[:12_000] + "\n…[truncated]"
            if len(content) > budget:
                content = content[:budget] + "\n…[truncated]"
            budget -= len(content)
            selected.append({"role": role, "content": content})
            if budget <= 0:
                break
        selected.reverse()
        return selected

    async def _execute_tool_calls(
        self,
        tool_calls_list: list[dict[str, Any]],
        *,
        seen_fps: set[str],
        result_cache: dict[str, str],
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Run tools in parallel (capped waves); always yield one tool msg per call id.

        Critical API contract: every ``tool_calls[].id`` on the preceding assistant
        message must receive a matching ``role=tool`` message. Cap and fingerprint
        dedupe may reduce *executions*, but never reduce *results*.
        """
        pending = normalize_tool_calls(tool_calls_list)
        if not pending:
            return

        # First occurrence of each fingerprint is the execution representative.
        fp_order: list[str] = []
        fp_to_tc: dict[str, dict[str, Any]] = {}
        for tc in pending:
            fp = _tool_call_fingerprint(tc)
            if fp not in fp_to_tc:
                fp_to_tc[fp] = tc
                fp_order.append(fp)

        async def _run_one(tc: dict[str, Any]) -> str:
            fn = tc.get("function") or {}
            name = (fn.get("name") or "").strip()
            raw_args = fn.get("arguments") or "{}"
            fp = _tool_call_fingerprint(tc)

            if fp in result_cache:
                return result_cache[fp]

            try:
                args = (
                    json.loads(raw_args)
                    if isinstance(raw_args, str)
                    else dict(raw_args)
                )
            except json.JSONDecodeError:
                args = {}
            if not isinstance(args, dict):
                args = {}

            result = await self.call_tool(ToolCall(tool_name=name, arguments=args))
            if result.success:
                payload = result.data
                content_str = (
                    payload
                    if isinstance(payload, str)
                    else json.dumps(payload, default=str)
                )
            else:
                content_str = result.error or format_tool_error(
                    "tool failed",
                    code="TOOL_FAILED",
                    tool_name=name or "unknown",
                    suggestion="Retry with corrected arguments or a different tool.",
                )
            if len(content_str) > 48_000:
                content_str = content_str[:48_000] + "\n…[tool output truncated]"
            result_cache[fp] = content_str
            seen_fps.add(fp)
            return content_str

        # Execute only fingerprints not already cached; never drop remainder past cap.
        to_run = [fp for fp in fp_order if fp not in result_cache]
        for wave_start in range(0, len(to_run), _MAX_PARALLEL_TOOLS):
            wave = to_run[wave_start : wave_start + _MAX_PARALLEL_TOOLS]
            for fp in wave:
                name = ((fp_to_tc[fp].get("function") or {}).get("name") or "").strip()
                yield f"@@tool_call:{name}", {}

            results = await asyncio.gather(
                *[_run_one(fp_to_tc[fp]) for fp in wave],
                return_exceptions=True,
            )
            for fp, item in zip(wave, results, strict=True):
                name = ((fp_to_tc[fp].get("function") or {}).get("name") or "").strip()
                if isinstance(item, BaseException):
                    logger.exception("parallel tool failed: %s", item)
                    content_str = format_tool_error(
                        str(item),
                        code="TOOL_EXCEPTION",
                        tool_name=name or "unknown",
                        suggestion=(
                            "Retry with corrected arguments or a different tool "
                            "(list_dir / file_read)."
                        ),
                    )
                    result_cache[fp] = content_str
                    seen_fps.add(fp)
                # Success path already wrote result_cache inside _run_one.

        # Always emit one tool result per original tool_call id (API contract).
        for tc in pending:
            fp = _tool_call_fingerprint(tc)
            name = ((tc.get("function") or {}).get("name") or "").strip()
            content_str = result_cache.get(
                fp,
                format_tool_error(
                    "tool produced no result",
                    code="TOOL_EMPTY",
                    tool_name=name or "unknown",
                    suggestion="Retry the tool or answer from context.",
                ),
            )
            call_id = tc.get("id") or str(uuid4())
            yield f"@@tool_result:{name or 'unknown'}", {
                "role": "tool",
                "tool_call_id": call_id,
                "content": content_str,
            }

    async def _call_llm_stream(
        self,
        message: str,
        session_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Call the LLM with a smooth ReAct loop (OpenCode-grade).

        Yields status tokens prefixed with '@@' for tool-call lifecycle events.
        Never leaves the user with a bare "tool limit" dead-end — final step
        always forces a plain-text answer (or a short synthesis).
        """
        try:
            from remedy.interfaces.attachments import build_multimodal_user_content

            context = await self._build_context()
            history = await self._load_session_history(session_id, message)
            # Memory Harness L0: prune send-view only (stored transcript untouched)
            with suppress(Exception):
                from remedy.memory.harness.pruner import prune_messages_for_send

                if self._harness_mode != "off":
                    history = prune_messages_for_send(history)
            user_content = build_multimodal_user_content(message, attachments)
            messages: list[dict[str, Any]] = [
                {
                    "role": "system",
                    "content": build_runtime_system_block(
                        system_prompt=self._system_prompt,
                        provider=self._llm_provider,
                        model=self._llm_model,
                        base_url=self._llm_base_url,
                        max_steps=self._max_react_steps,
                        context=context,
                    ),
                },
                *history,
                {"role": "user", "content": user_content},
            ]
            # Memory Harness auto: soft/strong compress nudge by fill estimate
            with suppress(Exception):
                if self._harness_mode == "auto":
                    from remedy.memory.harness.compressor import (
                        compression_nudge_message,
                        estimate_tokens,
                        should_nudge_compress,
                    )

                    level = should_nudge_compress(
                        estimate_tokens(messages),
                        min_pct=self._harness_min_pct,
                        max_pct=self._harness_max_pct,
                    )
                    if level:
                        messages.insert(-1, compression_nudge_message(level))
            all_tools = self._openai_tools()
            tools = (
                all_tools
                if should_enable_tools(
                    message, all_tools, has_attachments=bool(attachments)
                )
                else []
            )

            seen_fps: set[str] = set()
            result_cache: dict[str, str] = {}
            produced_user_text = False
            pseudo_recovery_done = False
            pseudo_nudge_count = 0
            # One automatic recovery nudge per turn after a failing tool batch.
            recovery_nudge_done = False
            headers = self._provider.auth_headers(self._llm_api_key)
            endpoint = self._provider.chat_endpoint(self._llm_base_url)

            # Long project reviews need headroom; cap overall wall so a runaway
            # stream cannot hang forever (sock_read still covers silence).
            timeout = aiohttp.ClientTimeout(total=900, sock_read=300, connect=30)
            connector = aiohttp.TCPConnector(
                limit=12,
                ttl_dns_cache=300,
            )
            # How many times we auto-continue after finish_reason=length / max_tokens.
            max_length_continuations = 6
            length_continuations = 0
            # Retry once after repairing DeepSeek reasoning_content on tool turns.
            reasoning_repair_done = False
            # Soft API errors: keep going when we already have tool context.
            api_soft_failures = 0
            max_api_soft_failures = 3
            # Sticky force-answer after recoverable provider failures.
            force_answer_sticky = False
            # One OAuth/API re-auth attempt per turn (xAI 401 → refresh token).
            auth_refresh_done = False
            async with aiohttp.ClientSession(
                timeout=timeout, connector=connector
            ) as http:
                for step in range(self._max_react_steps):
                    is_final_step = step >= self._max_react_steps - 1
                    # Force answer before the hard wall (and whenever tools disabled).
                    force_answer = (
                        is_final_step or not tools or force_answer_sticky
                    )
                    # Also force answer if we have already spent many tool steps
                    # and produced no visible text — keep UX snappy.
                    if step >= 8 and not produced_user_text:
                        force_answer = True
                    step_tools = None if force_answer else tools

                    if force_answer and step > 0 and length_continuations == 0:
                        # Don't inject "be concise" when we're mid length-continuation.
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Stop calling tools. Using the information above, "
                                    "give your complete final answer to the user now. "
                                    "Do not cut off mid-section."
                                ),
                            }
                        )

                    # Never send incomplete tool_calls/tool pairings (HTTP 400).
                    messages[:] = ensure_tool_call_pairings(messages)
                    # OpenAI-compatible providers (openai, deepseek, ollama, …) stream SSE.
                    # Anthropic currently uses a single JSON response (stream=False).
                    use_openai_sse = bool(
                        getattr(self._provider, "uses_openai_sse", True)
                    )
                    body = self._provider.build_body(
                        model=self._llm_model,
                        messages=messages,
                        tools=step_tools,
                        stream=use_openai_sse,
                    )

                    collected: dict[str, Any] = {"content": None, "tool_calls": None}
                    round_state = StreamRoundState()

                    async with http.post(
                        endpoint, headers=headers, json=body
                    ) as resp:
                        if resp.status != 200:
                            text = await resp.text()
                            logger.error(
                                "LLM API error %d: %s", resp.status, text[:500]
                            )
                            # xAI (and similar): expired OAuth → refresh once, retry.
                            if (
                                resp.status in (401, 403)
                                and not auth_refresh_done
                                and str(self._llm_provider or "").lower() == "xai"
                            ):
                                auth_refresh_done = True
                                try:
                                    from remedy.interfaces.xai_auth import (
                                        refresh_if_needed,
                                        resolve_bearer,
                                    )

                                    home = None
                                    if getattr(self, "config", None) is not None:
                                        hd = getattr(self.config, "home_dir", None)
                                        if hd:
                                            from pathlib import Path

                                            home = Path(hd).expanduser()
                                    refresh_if_needed(home)
                                    new_token = resolve_bearer(home)
                                    if new_token and new_token != self._llm_api_key:
                                        self._llm_api_key = new_token
                                        headers = self._provider.auth_headers(
                                            self._llm_api_key
                                        )
                                        logger.warning(
                                            "xAI credentials refreshed after HTTP %s; retrying",
                                            resp.status,
                                        )
                                        yield (
                                            "\n[auth] Refreshed xAI session; "
                                            "retrying request…\n"
                                        )
                                        continue
                                except Exception as auth_exc:
                                    logger.debug("xAI re-auth failed: %s", auth_exc)
                                # Refresh failed → clear soft-continue noise with guidance.
                                yield (
                                    "\n[auth required] xAI session expired or rejected. "
                                    "Sign in again in Settings (Sign in with xAI) or "
                                    "update your API key.\n"
                                )
                                return
                            # DeepSeek thinking mode: tool turns require reasoning_content.
                            if (
                                resp.status == 400
                                and "reasoning_content" in text.lower()
                                and not reasoning_repair_done
                            ):
                                reasoning_repair_done = True
                                if repair_reasoning_content_in_messages(messages):
                                    logger.warning(
                                        "Repaired missing reasoning_content on tool "
                                        "turns; retrying request"
                                    )
                                    yield (
                                        "\n[provider fix] Restored thinking-mode "
                                        "reasoning for tool turns; continuing…\n"
                                    )
                                    continue
                            api_soft_failures += 1
                            # Do not hard-stop the whole turn if we can still answer.
                            if api_soft_failures <= max_api_soft_failures:
                                yield (
                                    f"\n[LLM notice — HTTP {resp.status}; "
                                    f"continuing]\n{text[:240]}\n"
                                )
                                tools = []
                                force_answer_sticky = True
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "The model API returned an error. "
                                            "Using any tool results already gathered, "
                                            "give your best complete answer now. "
                                            "Do not call tools."
                                        ),
                                    }
                                )
                                continue
                            yield (
                                f"\n[LLM ERROR — HTTP {resp.status}]\n"
                                f"{text[:500]}\n[END LLM ERROR]\n"
                                "I hit repeated API errors but will try one last "
                                "answer from context.\n"
                            )
                            tools = []
                            force_answer_sticky = True
                            continue

                        # Live-stream tokens when tools are off (simple Qs / final answer).
                        # When tools are on, buffer text so "Let me check…" never jitters the UI.
                        stream_live = step_tools is None

                        headers_map = getattr(resp, "headers", None) or {}
                        content_type = str(
                            headers_map.get("Content-Type")
                            or headers_map.get("content-type")
                            or ""
                        ).lower()
                        # Prefer real response type; DeepSeek/OpenRouter return event-stream.
                        is_event_stream = "event-stream" in content_type
                        if use_openai_sse or is_event_stream:
                            content_iter = resp.content.__aiter__()
                            while True:
                                try:
                                    # Reap keep-alive-only streams that never end.
                                    line = await asyncio.wait_for(
                                        content_iter.__anext__(),
                                        timeout=120.0,
                                    )
                                except StopAsyncIteration:
                                    break
                                except TimeoutError:
                                    logger.warning(
                                        "SSE stream idle >120s; ending this model round"
                                    )
                                    break
                                line_text = line.decode("utf-8").strip()
                                if line_text == "data: [DONE]":
                                    break
                                chunk = parse_sse_data_line(line_text)
                                if chunk is None:
                                    continue
                                live = apply_openai_sse_chunk(
                                    round_state, chunk, stream_live=stream_live
                                )
                                if live:
                                    produced_user_text = True
                                    yield live
                        else:
                            data = await resp.json()
                            parsed = self._provider.extract_response(data)
                            content = parsed.get("content")
                            if content:
                                round_state.content_parts.append(content)
                            # Capture provider reasoning for tool-turn replay.
                            reason = (
                                parsed.get("reasoning_content")
                                or parsed.get("reasoning")
                                or ""
                            )
                            if isinstance(reason, str) and reason.strip():
                                round_state.reasoning_parts.append(reason.strip())
                            raw_tcs = parsed.get("tool_calls")
                            if raw_tcs:
                                round_state.tool_call_acc = dict(enumerate(raw_tcs))
                            collected = {**collected, **parsed}

                        content_parts = round_state.content_parts
                        reasoning_parts = round_state.reasoning_parts

                    tool_calls_list = round_state.tool_calls_list(collected)
                    reasoning_out = round_state.reasoning_out

                    # Finalize text. Live-stream already yielded tokens when tools off.
                    text_out = finalize_round_text(round_state, tool_calls_list)
                    if (
                        text_out
                        and stream_live
                        and not content_parts
                        and reasoning_parts
                        and not tool_calls_list
                        and not _looks_like_pseudo_tools(text_out)
                    ):
                        yield text_out
                        produced_user_text = True
                    if text_out:
                        collected["content"] = text_out

                    # Recovery: model wrote tool calls as plain text → run them for real.
                    if (
                        not tool_calls_list
                        and text_out
                        and _looks_like_pseudo_tools(text_out)
                        and all_tools
                        and not pseudo_recovery_done
                        and not force_answer
                    ):
                        recovered = _parse_pseudo_tool_calls(text_out)
                        if recovered:
                            pseudo_recovery_done = True
                            tools = all_tools  # ensure schemas stay available
                            recovered = normalize_tool_calls(recovered)
                            yield "@@tool_calls"
                            messages.append(
                                build_assistant_api_message(
                                    content=(
                                        "I'll use tools to inspect the project "
                                        "(recovering from text-form tool calls)."
                                    ),
                                    tool_calls=recovered,
                                    reasoning_content=reasoning_out or None,
                                )
                            )
                            batch_tool_msgs: list[dict[str, Any]] = []
                            async for event, tool_msg in self._execute_tool_calls(
                                recovered,
                                seen_fps=seen_fps,
                                result_cache=result_cache,
                            ):
                                if event.startswith("@@"):
                                    yield event
                                if tool_msg:
                                    messages.append(tool_msg)
                                    batch_tool_msgs.append(tool_msg)
                            if (
                                not recovery_nudge_done
                                and batch_has_tool_errors(batch_tool_msgs)
                            ):
                                recovery_nudge_done = True
                                messages.append(recovery_nudge_message())
                            continue

                    if text_out and (not tool_calls_list or force_answer):
                        # Don't ship faux tool syntax as the final answer.
                        if (
                            _looks_like_pseudo_tools(text_out)
                            and all_tools
                            and not force_answer
                            and pseudo_nudge_count < 1
                            and not pseudo_recovery_done
                        ):
                            pseudo_nudge_count += 1
                            tools = all_tools
                            messages.append(
                                {
                                    "role": "user",
                                    "content": (
                                        "Do not write tool calls as text. "
                                        "Use the function-calling API now "
                                        "(file_read / list_dir / bash_exec), "
                                        "or answer from context."
                                    ),
                                }
                            )
                            continue
                        if stream_live and produced_user_text:
                            # Already streamed live; if it was pseudo-tool junk, apologize once.
                            if _looks_like_pseudo_tools(text_out):
                                # Tools were off during live stream — re-enable and recover.
                                if all_tools and not pseudo_recovery_done:
                                    recovered = normalize_tool_calls(
                                        _parse_pseudo_tool_calls(text_out)
                                    )
                                    if recovered:
                                        pseudo_recovery_done = True
                                        tools = all_tools
                                        messages.append(
                                            build_assistant_api_message(
                                                content=text_out[:500],
                                                tool_calls=recovered,
                                                reasoning_content=reasoning_out or None,
                                            )
                                        )
                                        async for event, tool_msg in self._execute_tool_calls(
                                            recovered,
                                            seen_fps=seen_fps,
                                            result_cache=result_cache,
                                        ):
                                            if event.startswith("@@"):
                                                yield event
                                            if tool_msg:
                                                messages.append(tool_msg)
                                        continue
                            # Hit max_tokens mid-answer → seamless continuation.
                            if (
                                round_state.hit_length_limit
                                and length_continuations < max_length_continuations
                                and not tool_calls_list
                            ):
                                length_continuations += 1
                                logger.info(
                                    "Stream hit length limit (finish_reason=%s); "
                                    "auto-continuing (%d/%d)",
                                    round_state.finish_reason,
                                    length_continuations,
                                    max_length_continuations,
                                )
                                messages.append(
                                    {"role": "assistant", "content": text_out}
                                )
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "Your previous message was cut off by the output "
                                            "token limit. Continue exactly where you stopped — "
                                            "do not restart, renumber from scratch, or summarize "
                                            "what you already wrote. Pick up mid-sentence if needed."
                                        ),
                                    }
                                )
                                tools = []  # keep producing prose
                                continue
                            return
                        if not stream_live:
                            yield text_out
                            produced_user_text = True
                            if (
                                round_state.hit_length_limit
                                and length_continuations < max_length_continuations
                                and not tool_calls_list
                            ):
                                length_continuations += 1
                                messages.append(
                                    {"role": "assistant", "content": text_out}
                                )
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "Continue exactly where you stopped — do not restart."
                                        ),
                                    }
                                )
                                tools = []
                                continue
                        return

                    if not tool_calls_list or force_answer:
                        # Nothing useful produced — soft empty, not a tool-limit scare.
                        if not produced_user_text:
                            yield (
                                "I couldn't produce a complete answer from the available "
                                "context. Try a more specific request or point me at a file."
                            )
                        return

                    # Filter out exact repeats of prior tool calls this turn.
                    fresh_calls = normalize_tool_calls(
                        filter_fresh_tool_calls(tool_calls_list, seen_fps)
                    )
                    if not fresh_calls:
                        # Model is looping the same tools — force a final answer next.
                        looped = normalize_tool_calls(tool_calls_list)
                        messages.append(
                            build_assistant_api_message(
                                content=collected.get("content"),
                                tool_calls=looped,
                                reasoning_content=reasoning_out or "",
                            )
                        )
                        for tc in looped:
                            fp = _tool_call_fingerprint(tc)
                            cached = result_cache.get(fp, "(already retrieved)")
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc["id"],
                                    "content": cached,
                                }
                            )
                        # Jump toward final answer on next iteration.
                        tools = []  # disable further tool schemas
                        continue

                    messages.append(
                        build_assistant_api_message(
                            content=collected.get("content"),
                            tool_calls=fresh_calls,
                            # DeepSeek thinking mode: MUST pass reasoning back on tool turns.
                            reasoning_content=reasoning_out or "",
                        )
                    )

                    batch_tool_msgs: list[dict[str, Any]] = []
                    async for event, tool_msg in self._execute_tool_calls(
                        fresh_calls,
                        seen_fps=seen_fps,
                        result_cache=result_cache,
                    ):
                        if event.startswith("@@"):
                            yield event
                        if tool_msg:
                            messages.append(tool_msg)
                            batch_tool_msgs.append(tool_msg)

                    logger.debug(
                        "ReAct step %d executed %d tool call(s)",
                        step + 1,
                        len(fresh_calls),
                    )

                    # Soft recovery: if tools failed, nudge the model once to
                    # try alternate paths/commands before answering.
                    if (
                        not recovery_nudge_done
                        and not force_answer
                        and batch_has_tool_errors(batch_tool_msgs)
                    ):
                        recovery_nudge_done = True
                        messages.append(recovery_nudge_message())
                        logger.info(
                            "Injected tool recovery nudge after step %d (RECOVERY_NUDGE)",
                            step + 1,
                        )

            # Exhausted steps without a streamed answer — synthesize briefly.
            if not produced_user_text:
                # One last non-tool call with whatever tool results we have.
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Give a short final answer now based on the tool results above."
                        ),
                    }
                )
                messages[:] = ensure_tool_call_pairings(messages)
                use_openai_sse = bool(
                    getattr(self._provider, "uses_openai_sse", True)
                )
                body = self._provider.build_body(
                    model=self._llm_model,
                    messages=messages,
                    tools=None,
                    stream=use_openai_sse,
                )
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=60)
                    ) as http2, http2.post(
                        endpoint, headers=headers, json=body
                    ) as resp:
                        if resp.status == 200:
                            headers_map = getattr(resp, "headers", None) or {}
                            content_type = str(
                                headers_map.get("Content-Type")
                                or headers_map.get("content-type")
                                or ""
                            ).lower()
                            if use_openai_sse or "event-stream" in content_type:
                                async for line in resp.content:
                                    line_text = line.decode("utf-8").strip()
                                    if not line_text or line_text.startswith(":"):
                                        continue
                                    if line_text == "data: [DONE]":
                                        break
                                    if line_text.startswith("data: "):
                                        line_text = line_text[6:]
                                    try:
                                        chunk = json.loads(line_text)
                                    except json.JSONDecodeError:
                                        continue
                                    delta = (chunk.get("choices") or [{}])[0].get(
                                        "delta"
                                    ) or {}
                                    piece = delta.get("content")
                                    if piece:
                                        produced_user_text = True
                                        yield piece
                            else:
                                data = await resp.json()
                                parsed = self._provider.extract_response(data)
                                piece = parsed.get("content")
                                if piece:
                                    produced_user_text = True
                                    yield str(piece)
                except Exception:
                    logger.debug("final synthesis failed", exc_info=True)
            if not produced_user_text:
                yield (
                    "Done exploring — I don't have enough signal for a confident answer. "
                    "Point me at a specific file or error and I'll dig in."
                )
        except Exception as e:
            logger.exception("LLM stream failed")
            # Never leave the user with only a stack-looking error — give a path forward.
            yield (
                f"\n[LLM STREAM EXCEPTION]\n{e}\n[END LLM STREAM EXCEPTION]\n\n"
                "Something went wrong talking to the model mid-turn. "
                "Try again, switch model, or ask a narrower question. "
                "Your session history is intact."
            )

    async def _post_chat(
        self, body: dict[str, Any]
    ) -> dict[str, Any] | str:
        headers = self._provider.auth_headers(self._llm_api_key)
        endpoint = self._provider.chat_endpoint(self._llm_base_url)

        async with (
            aiohttp.ClientSession() as session,
            session.post(
                endpoint,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp,
        ):
            if resp.status != 200:
                text = await resp.text()
                # One refresh attempt for expired xAI OAuth tokens.
                if (
                    resp.status in (401, 403)
                    and str(self._llm_provider or "").lower() == "xai"
                ):
                    try:
                        from pathlib import Path

                        from remedy.interfaces.xai_auth import (
                            refresh_if_needed,
                            resolve_bearer,
                        )

                        home = None
                        if getattr(self, "config", None) is not None:
                            hd = getattr(self.config, "home_dir", None)
                            if hd:
                                home = Path(hd).expanduser()
                        refresh_if_needed(home)
                        new_token = resolve_bearer(home)
                        if new_token and new_token != self._llm_api_key:
                            self._llm_api_key = new_token
                            headers = self._provider.auth_headers(self._llm_api_key)
                            async with session.post(
                                endpoint,
                                headers=headers,
                                json=body,
                                timeout=aiohttp.ClientTimeout(total=60),
                            ) as resp2:
                                if resp2.status == 200:
                                    return await resp2.json()
                                text = await resp2.text()
                                logger.error(
                                    "LLM API error %d after reauth: %s",
                                    resp2.status,
                                    text[:500],
                                )
                                return (
                                    "\n[auth required] xAI session expired. "
                                    "Sign in again (Settings or `remedy auth login xai`).\n"
                                )
                    except Exception as auth_exc:
                        logger.debug("xAI re-auth in _post_chat failed: %s", auth_exc)
                logger.error("LLM API error %d: %s", resp.status, text[:500])
                return f"\n[LLM ERROR — HTTP {resp.status}]\n{text[:500]}\n[END LLM ERROR]"
            return await resp.json()

    async def _apply_session_workspace(self, session_id: str | None) -> None:
        """Bind tools/cwd to the session project path (else default config path)."""
        if session_id:
            self._session_id = session_id
        session_path: str | None = None
        if session_id and self.memory is not None:
            with suppress(Exception):
                sess = await self.memory.get_chat_session(session_id)
                if sess is not None:
                    session_path = getattr(sess, "project_path", None)
        if session_path and str(session_path).strip():
            self.set_project_path(session_path, as_default=False)
        else:
            self._active_project_path = self._default_project_path

    async def stream_response(
        self,
        message: str,
        session_id: str | None = None,
        model: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens from the LLM for real-time SSE delivery.

        Yields individual tokens as they arrive from the provider.
        Tool-call lifecycle events are prefixed with '@@'.
        Falls back to the echo-style fallback when no API key is configured.
        """
        await self._apply_session_workspace(session_id)

        prev_model = self._llm_model
        if model and str(model).strip():
            self._llm_model = str(model).strip()

        try:
            if not self._llm_api_key:
                yield (
                    "[LLM not connected — no API key. "
                    "Open Settings, enter your provider key, Save, then resend.]\n"
                )
                return

            async for chunk in self._call_llm_stream(
                message, session_id=session_id, attachments=attachments
            ):
                yield chunk
        finally:
            self._llm_model = prev_model

    async def _build_context(self) -> str:
        parts = []
        # Project workspace (OpenCode-style default directory for this session)
        with suppress(Exception):
            parts.append(
                workspace_context_block(
                    self.effective_project_path(),
                    access_scope=self.access_scope(),
                    extra_roots=self.allowed_roots(),
                )
            )

        # User profile (companion personalization)
        with suppress(Exception):
            if self.memory is not None:
                profile = await self.memory.get_or_create_profile()
                profile_lines: list[str] = []
                if profile.display_name:
                    profile_lines.append(f"- Name: {profile.display_name}")
                for key, trait in list(profile.traits.items())[:12]:
                    if trait.confidence >= 0.4:
                        profile_lines.append(f"- {key}: {trait.value}")
                for fact in profile.facts[-8:]:
                    if fact.confidence >= 0.5:
                        profile_lines.append(f"- ({fact.category}) {fact.fact}")
                if profile_lines:
                    parts.append(
                        "User profile (remember across sessions):\n"
                        + "\n".join(profile_lines)
                    )

        # Session Brief (Memory Harness L2) when present on agent
        with suppress(Exception):
            from remedy.memory.harness.brief import brief_to_context_block

            brief = getattr(self, "_session_brief", None)
            block = brief_to_context_block(brief)
            if block:
                parts.append(block)

        recent: list[Any] = []
        with suppress(Exception):
            # Keep short — large memory dumps push weak models into pointless tool loops.
            # Prefer query-time search later; recent is a light fallback.
            recent = await self.memory.list_recent(limit=6)
        if recent:
            lines = []
            for e in recent:
                content = (e.content or "").strip()
                # Skip noisy fallback/self-chat noise that poisons simple answers.
                if "fallback mode" in content.lower() or content.startswith("Received:"):
                    continue
                if content.startswith("User (") or content.startswith("Remedy:"):
                    # Gateway echo memories — skip; session history covers chat.
                    continue
                ts = e.created_at.isoformat()[:19] if e.created_at else "?"
                lines.append(f"[{ts}] {content[:140]}")
            if lines:
                parts.append(
                    "Recent memory (optional):\n" + "\n".join(lines[-4:])
                )

        tools = self.tool_registry.tools
        if tools:
            names = ", ".join(t.name for t in tools)
            parts.append(
                f"Built-in tools (executable): {names}."
            )

        # Skills registry — so "what skills do you have?" never needs a shell.
        with suppress(Exception):
            reg = getattr(self, "skills", None)
            count = int(getattr(reg, "count", 0) or 0) if reg is not None else 0
            if reg is not None and count > 0:
                skill_lines = reg.summary_lines(limit=40)
                parts.append(
                    "Skills loaded (procedure packs — list these when asked):\n"
                    + "\n".join(skill_lines)
                )
            else:
                parts.append(
                    "Skills loaded: (none yet — bundled defaults load on server start)."
                )

        return "\n\n".join(parts)

    def _fallback_response(self, message: str, event: GatewayEvent) -> str:
        msg_lower = message.lower().strip()

        greetings = {"hello", "hi", "hey", "greetings", "yo"}
        words = set(msg_lower.rstrip("!.,?").split())
        if msg_lower in greetings or words & greetings:
            return f"Hello! I'm {self.config.name}. How can I help you?"

        if "help" in msg_lower or "?" in msg_lower:
            return (
                "I'm a basic agent runtime. I can remember conversations in my "
                "persistent store. Try using memory commands or tools if available."
            )

        if "remember" in msg_lower or "memory" in msg_lower:
            return "I've stored our conversation in memory. I can recall it later if needed."

        return (
            f"Received: {message[:200]}. "
            f"I'm running in fallback mode. Set an LLM API key (via config or "
            f"REMEDY_LLM_API_KEY env var) for intelligent responses."
        )
