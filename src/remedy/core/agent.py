"""Concrete agent runtime -- BasicRuntime with LLM integration and ReAct tool use.

Provides the default Remedy agent: a multi-step ReAct loop that stores conversation
in memory, calls LLM providers through the adapter layer, and invokes tools
through the ToolRegistry.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from uuid import uuid4

import aiohttp

from pathlib import Path

from remedy.core.providers import ProviderAdapter, get_provider
from remedy.core.runtime import AgentRuntime
from remedy.core.security import check_dangerous_command
from remedy.core.workspace import (
    ensure_project_dir,
    jail_path,
    resolve_project_path,
    workspace_context_block,
)
from remedy.interfaces.config import persona_system_addendum
from remedy.memory.store import MemoryStore
from remedy.models import (
    AgentConfig,
    ChannelKind,
    ChatMessageRole,
    EventKind,
    GatewayEvent,
    ToolCall,
    ToolResult,
)
from remedy.skills.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)


from remedy.core.errors import format_tool_error
from remedy.core.react_policy import (
    HISTORY_CHAR_BUDGET as _HISTORY_CHAR_BUDGET,
    HISTORY_MSG_LIMIT as _HISTORY_MSG_LIMIT,
    MAX_PARALLEL_TOOLS as _MAX_PARALLEL_TOOLS,
    MAX_REACT_STEPS as _MAX_REACT_STEPS,
    RECOVERY_NUDGE,
    _DEFAULT_SYSTEM_PROMPT,
    _build_system_prompt,
    _looks_like_pseudo_tools,
    _message_wants_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
    batch_has_tool_errors,
    build_system_prompt,
    looks_like_pseudo_tools,
    message_wants_tools,
    parse_pseudo_tool_calls,
    recovery_nudge_message,
    tool_call_fingerprint,
    tool_content_is_error,
)
from remedy.core.react_stream import (
    StreamRoundState,
    apply_openai_sse_chunk,
    build_runtime_system_block,
    filter_fresh_tool_calls,
    finalize_round_text,
    parse_sse_data_line,
    should_enable_tools,
)

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
        self._register_workspace_tools()

    def effective_project_path(self) -> Path:
        """Active workspace root for tools / context (session or default)."""
        try:
            return ensure_project_dir(self._active_project_path)
        except Exception:
            return resolve_project_path(None)

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
            target = jail_path(path, root)
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
            return data

        async def file_write(path: str, content: str = "") -> str:
            root = self.effective_project_path()
            target = jail_path(path, root)
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
                        "and ensure the path is inside the project workspace."
                    ),
                )
            return f"Wrote {len(content)} bytes to {path}"

        async def list_dir(path: str = ".") -> str:
            root = self.effective_project_path()
            target = jail_path(path, root)
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
                    rel = p.relative_to(root).as_posix()
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
            root = self.effective_project_path()
            argv = [*win_shell_prefix(), command]
            sandbox = SubprocessSandbox(allowed_paths=[root])
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
            "Read a text file under the project working directory. Path is relative to the project root.",
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
            "Write a text file under the project working directory.",
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
            "List files and directories under the project working directory.",
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
    ) -> None:
        """Hot-apply LLM / persona / project settings without restarting."""
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

        out: list[dict[str, Any]] = []
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
                # Soft-trim huge prior answers
                if len(content) > 4000:
                    content = content[:4000] + "\n…[truncated]"
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
        """Run tools in parallel (capped), dedupe repeats, yield (event, tool_msg)."""

        async def _one(tc: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
            fn = tc.get("function") or {}
            name = (fn.get("name") or "").strip()
            raw_args = fn.get("arguments") or "{}"
            fp = _tool_call_fingerprint(tc)
            call_id = tc.get("id") or str(uuid4())

            if fp in result_cache:
                content_str = result_cache[fp]
                return name, content_str, {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": content_str,
                }

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
                # Prefer already-formatted error strings (with Suggestion lines).
                content_str = result.error or format_tool_error(
                    "tool failed",
                    code="TOOL_FAILED",
                    tool_name=name or "unknown",
                    suggestion="Retry with corrected arguments or a different tool.",
                )
            # Cap tool payloads — keeps multi-step turns fast and under context limits.
            if len(content_str) > 48_000:
                content_str = content_str[:48_000] + "\n…[tool output truncated]"
            result_cache[fp] = content_str
            seen_fps.add(fp)
            return name, content_str, {
                "role": "tool",
                "tool_call_id": call_id,
                "content": content_str,
            }

        # Deduplicate within this batch (keep first of each fingerprint).
        unique: list[dict[str, Any]] = []
        batch_seen: set[str] = set()
        for tc in tool_calls_list:
            name = ((tc.get("function") or {}).get("name") or "").strip()
            if not name:
                continue
            fp = _tool_call_fingerprint(tc)
            if fp in batch_seen:
                continue
            batch_seen.add(fp)
            unique.append(tc)
            if len(unique) >= _MAX_PARALLEL_TOOLS:
                break

        if not unique:
            return

        # Notify UI of starts, then run in parallel for speed.
        for tc in unique:
            name = ((tc.get("function") or {}).get("name") or "").strip()
            yield f"@@tool_call:{name}", {}

        results = await asyncio.gather(
            *[_one(tc) for tc in unique], return_exceptions=True
        )
        for item in results:
            if isinstance(item, BaseException):
                logger.exception("parallel tool failed: %s", item)
                err_msg = {
                    "role": "tool",
                    "tool_call_id": str(uuid4()),
                    "content": format_tool_error(
                        str(item),
                        code="TOOL_EXCEPTION",
                        tool_name="unknown",
                        suggestion=(
                            "Retry with corrected arguments or a different tool "
                            "(list_dir / file_read)."
                        ),
                    ),
                }
                yield "@@tool_result:error", err_msg
                continue
            name, _content, tool_msg = item
            yield f"@@tool_result:{name}", tool_msg

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

            # Long project reviews need headroom: no short total wall that kills mid-stream.
            # sock_read only trips when the provider goes silent (not while tokens keep flowing).
            # total=None → no overall deadline; connect is still bounded.
            timeout = aiohttp.ClientTimeout(total=None, sock_read=300, connect=30)
            connector = aiohttp.TCPConnector(
                limit=12,
                ttl_dns_cache=300,
            )
            # How many times we auto-continue after finish_reason=length / max_tokens.
            max_length_continuations = 4
            length_continuations = 0
            async with aiohttp.ClientSession(
                timeout=timeout, connector=connector
            ) as http:
                for step in range(self._max_react_steps):
                    is_final_step = step >= self._max_react_steps - 1
                    # Force answer before the hard wall (and whenever tools disabled).
                    force_answer = is_final_step or not tools
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

                    body = self._provider.build_body(
                        model=self._llm_model,
                        messages=messages,
                        tools=step_tools,
                        stream=True,
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
                            yield (
                                f"\n[LLM ERROR — HTTP {resp.status}]\n"
                                f"{text[:500]}\n[END LLM ERROR]"
                            )
                            return

                        # Live-stream tokens when tools are off (simple Qs / final answer).
                        # When tools are on, buffer text so "Let me check…" never jitters the UI.
                        stream_live = step_tools is None

                        if self._provider.provider_name == "openai":
                            async for line in resp.content:
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
                            raw_tcs = parsed.get("tool_calls")
                            if raw_tcs:
                                round_state.tool_call_acc = dict(enumerate(raw_tcs))
                            collected = {**collected, **parsed}

                        content_parts = round_state.content_parts
                        reasoning_parts = round_state.reasoning_parts
                        tool_call_acc = round_state.tool_call_acc

                    tool_calls_list = round_state.tool_calls_list(collected)

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
                            yield "@@tool_calls"
                            messages.append(
                                {
                                    "role": "assistant",
                                    "content": (
                                        "I'll use tools to inspect the project "
                                        "(recovering from text-form tool calls)."
                                    ),
                                    "tool_calls": recovered,
                                }
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
                                    recovered = _parse_pseudo_tool_calls(text_out)
                                    if recovered:
                                        pseudo_recovery_done = True
                                        tools = all_tools
                                        messages.append(
                                            {
                                                "role": "assistant",
                                                "content": text_out[:500],
                                                "tool_calls": recovered,
                                            }
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
                    fresh_calls = filter_fresh_tool_calls(tool_calls_list, seen_fps)
                    if not fresh_calls:
                        # Model is looping the same tools — force a final answer next.
                        messages.append(
                            {
                                "role": "assistant",
                                "content": collected.get("content"),
                                "tool_calls": tool_calls_list,
                            }
                        )
                        for tc in tool_calls_list:
                            fp = _tool_call_fingerprint(tc)
                            cached = result_cache.get(fp, "(already retrieved)")
                            messages.append(
                                {
                                    "role": "tool",
                                    "tool_call_id": tc.get("id") or str(uuid4()),
                                    "content": cached,
                                }
                            )
                        # Jump toward final answer on next iteration.
                        tools = []  # disable further tool schemas
                        continue

                    messages.append(
                        {
                            "role": "assistant",
                            "content": collected.get("content"),
                            "tool_calls": fresh_calls,
                        }
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
                body = self._provider.build_body(
                    model=self._llm_model,
                    messages=messages,
                    tools=None,
                    stream=True,
                )
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=60)
                    ) as http2:
                        async with http2.post(
                            endpoint, headers=headers, json=body
                        ) as resp:
                            if resp.status == 200 and self._provider.provider_name == "openai":
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
                except Exception:
                    logger.debug("final synthesis failed", exc_info=True)
            if not produced_user_text:
                yield (
                    "Done exploring — I don't have enough signal for a confident answer. "
                    "Point me at a specific file or error and I'll dig in."
                )
        except Exception as e:
            logger.exception("LLM stream failed")
            yield f"\n[LLM STREAM EXCEPTION]\n{e}\n[END LLM STREAM EXCEPTION]"

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
            parts.append(workspace_context_block(self.effective_project_path()))

        recent: list[Any] = []
        with suppress(Exception):
            # Keep short — large memory dumps push weak models into pointless tool loops.
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
