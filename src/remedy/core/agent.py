"""Concrete agent runtime -- BasicRuntime with LLM integration and ReAct tool use.

Provides the default Remedy agent: a multi-step ReAct loop that stores conversation
in memory, calls LLM providers through the adapter layer, and invokes tools
through the ToolRegistry.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.errors import SecurityError, format_tool_error
from remedy.core.providers import ProviderAdapter, get_provider
from remedy.core.react_policy import (
    MAX_PARALLEL_TOOLS as _MAX_PARALLEL_TOOLS,
)
from remedy.core.react_policy import (
    MAX_REACT_STEPS as _MAX_REACT_STEPS,
)
from remedy.core.react_policy import (
    _build_system_prompt,
    _looks_like_pseudo_tools,
    _message_wants_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
    message_wants_tools,
    tool_content_is_error,
)
from remedy.core.runtime import AgentRuntime
from remedy.core.workspace import (
    allowed_roots_for_scope,
    effective_access_scope,
    ensure_project_dir,
    is_unset_project_path,
    normalize_access_scope,
    resolve_project_path,
    resolve_under_roots,
)
from remedy.memory.store import MemoryStore
from remedy.models import (
    AgentConfig,
    GatewayEvent,
    ToolCall,
    ToolResult,
)
from remedy.skills.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

# Re-export for tests / external importers that still target remedy.core.agent
__all__ = [
    "BasicRuntime",
    "_MAX_PARALLEL_TOOLS",
    "_MAX_REACT_STEPS",
    "_looks_like_pseudo_tools",
    "_message_wants_tools",
    "_parse_pseudo_tool_calls",
    "_tool_call_fingerprint",
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
        # Empty / "." project → home as root + full access (see workspace.effective_access_scope).
        raw_proj = getattr(config, "project_path", None)
        self._project_path_raw: str | None = (
            None if raw_proj is None else str(raw_proj)
        )
        self._default_project_path: Path = resolve_project_path(self._project_path_raw)
        self._active_project_path: Path = self._default_project_path
        self._access_scope: str = normalize_access_scope(
            getattr(config, "access_scope", None) or "project"
        )
        self._harness_mode: str = (
            str(getattr(config, "harness_mode", None) or "auto").strip().lower()
        )
        # Stay hands-off until context is genuinely full (was 0.35/0.70 — too early).
        self._harness_min_pct: float = float(
            getattr(config, "harness_min_context_pct", None) or 0.75
        )
        self._harness_max_pct: float = float(
            getattr(config, "harness_max_context_pct", None) or 0.92
        )
        # Default high — medium/off were throttling max_tokens and truncating reasoning.
        tl = str(getattr(config, "thinking_level", None) or "high").strip().lower()
        self._thinking_level: str = (
            tl if tl in ("off", "low", "medium", "high") else "high"
        )
        am = str(getattr(config, "approval_mode", None) or "ask").strip().lower()
        self._approval_mode: str = am if am in ("ask", "auto") else "ask"
        with suppress(Exception):
            from remedy.core.approvals import APPROVALS

            APPROVALS.set_mode(self._approval_mode)
        # Memory Harness L2 working state (per agent instance / session)
        self._session_brief = None  # type: ignore[assignment]
        self._register_workspace_tools()
        self._register_memory_tools()

    def project_path_is_unset(self) -> bool:
        """True when no real project folder is configured (→ full access)."""
        return is_unset_project_path(getattr(self, "_project_path_raw", None))

    def effective_project_path(self) -> Path:
        """Active workspace root for tools / context (session or default)."""
        try:
            return ensure_project_dir(self._active_project_path)
        except Exception:
            return resolve_project_path(None)

    def access_scope(self) -> str:
        """Configured scope, or **full** when no project folder is set."""
        return effective_access_scope(
            getattr(self, "_access_scope", None),
            getattr(self, "_project_path_raw", None),
        )

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
        raw = None if path is None else str(path)
        if is_unset_project_path(raw):
            self._project_path_raw = None
            resolved = resolve_project_path(None)
        else:
            self._project_path_raw = raw.strip() if raw else None
            resolved = resolve_project_path(
                raw,
                fallback=self._default_project_path,
            )
        with suppress(Exception):
            resolved = ensure_project_dir(resolved)
        self._active_project_path = resolved
        if as_default:
            self._default_project_path = resolved
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    # Persist empty as "" so UI shows unset, not a guessed home path.
                    self.config.project_path = (
                        "" if self.project_path_is_unset() else str(resolved)
                    )
        return resolved

    def _register_workspace_tools(self) -> None:
        """Register file/shell tools jailed to the project workspace."""
        from remedy.core.agent_workspace_tools import register_workspace_tools

        register_workspace_tools(self)

    def _get_learning_loop(self):
        """Lazy LearningLoop bound to home skills dir + this registry."""
        if self._learning_loop is not None:
            return self._learning_loop
        try:
            from remedy.core.learning_loop import LearningLoop

            home = Path(
                getattr(self.config, "home_dir", None) or "~/.remedy"
            ).expanduser()
            skills_dir = home / "skills"
            self._learning_loop = LearningLoop(
                skills_dir=skills_dir,
                memory=self.memory,
                registry=getattr(self, "skills", None),
            )
        except Exception:
            logger.debug("LearningLoop init failed", exc_info=True)
            return None
        return self._learning_loop

    def _register_skill_tools(self) -> None:
        """Progressive disclosure skill tools."""
        from remedy.core.agent_skill_tools import register_skill_tools

        register_skill_tools(self)

    def _register_local_discover_tools(self) -> None:
        from remedy.core.agent_local_tools import register_local_discover_tools

        register_local_discover_tools(self)

    def _register_comfyui_tools(self) -> None:
        from remedy.core.agent_local_tools import register_comfyui_tools

        register_comfyui_tools(self)

    def _register_vision_tools(self) -> None:
        from remedy.core.agent_local_tools import register_vision_tools

        register_vision_tools(self)

    def _register_memory_tools(self) -> None:
        """Memory + harness + goals/plans/checkpoints."""
        from remedy.core.agent_memory_tools import register_memory_tools

        register_memory_tools(self)

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
        thinking_level: str | None = None,
        approval_mode: str | None = None,
    ) -> None:
        """Hot-apply LLM / persona / project settings without restarting."""
        if thinking_level is not None:
            tl = str(thinking_level).strip().lower()
            self._thinking_level = tl if tl in ("off", "low", "medium", "high") else "high"
        if approval_mode is not None:
            try:
                from remedy.core.approvals import APPROVALS

                self._approval_mode = APPROVALS.set_mode(str(approval_mode))
            except Exception:
                self._approval_mode = str(approval_mode).strip().lower() or "ask"
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
        _prov_changed = False
        old_provider = getattr(self, "_llm_provider", None)
        old_model = getattr(self, "_llm_model", None)
        if provider is not None and provider.strip():
            new_p = provider.strip().lower()
            _prov_changed = new_p != old_provider
            self._llm_provider = new_p
            self._provider = get_provider(self._llm_provider)
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    self.config.llm_provider = self._llm_provider
        if model is not None and model.strip():
            new_m = model.strip()
            _prov_changed = _prov_changed or new_m != old_model
            self._llm_model = new_m
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    self.config.llm_model = self._llm_model
        if base_url is not None and base_url.strip():
            self._llm_base_url = base_url.strip()
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    self.config.llm_base_url = self._llm_base_url
        if _prov_changed:
            with suppress(Exception):
                from remedy.nanoswarm import get_swarm
                from remedy.nanoswarm.events import SwarmEvent

                msgs = getattr(self, "_last_send_messages", None)
                get_swarm().dispatch(
                    SwarmEvent.provider_changed(
                        getattr(self, "_llm_provider", "") or "",
                        model=getattr(self, "_llm_model", None),
                        old_provider=old_provider,
                        old_model=old_model,
                        session_id=str(getattr(self, "_session_id", "") or ""),
                    ),
                    messages=msgs if isinstance(msgs, list) else None,
                    session_id=str(getattr(self, "_session_id", "") or ""),
                    old_provider=old_provider,
                    old_model=old_model,
                    min_pct=float(getattr(self, "_harness_min_pct", 0.75) or 0.75),
                    max_pct=float(getattr(self, "_harness_max_pct", 0.92) or 0.92),
                )
        if api_key is not None:
            # Empty string means leave unchanged (UI "keep current" path).
            if api_key != "":
                self._llm_api_key = api_key
                if hasattr(self, "config") and self.config is not None:
                    with suppress(Exception):
                        self.config.llm_api_key = self._llm_api_key
        if persona is not None:
            p = persona.strip().lower() if persona.strip() else "default"
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    self.config.persona = p
            self._system_prompt = _build_system_prompt(p)
        if name is not None and name.strip():
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    self.config.name = name.strip()
        if project_path is not None:
            # Empty string clears project → home root + full access.
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
        # Plan mode: refuse mutating tools even if they slipped into the schema
        if getattr(self, "_plan_mode", False):
            from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES

            if name not in PLAN_MODE_TOOL_NAMES:
                return ToolResult(
                    call_id=tool_call.id,
                    success=False,
                    error=format_tool_error(
                        f"Tool '{name}' blocked in Plan mode",
                        code="PLAN_MODE_BLOCKED",
                        tool_name=name,
                        suggestion="Switch to Build mode (Ctrl+B) to run shell/file tools.",
                    ),
                )
        default_registry.counter("remedy_tool_calls_total", tool=name).inc()
        t0 = _time.perf_counter()
        try:
            # First positional must NOT be called "name" — many tools take a
            # `name` argument (skill_activate, skill_run) and would raise
            # TypeError: multiple values for argument 'name'.
            result = await self.tool_registry.execute(
                tool_name=name,
                **(tool_call.arguments or {}),
            )
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
        from remedy.core.agent_llm import openai_tools_payload

        return openai_tools_payload(self.tool_registry)

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
        """Load recent user/assistant turns for multi-turn continuity."""
        from remedy.core.agent_history import load_session_history

        return await load_session_history(self.memory, session_id, current_user)

    async def _execute_tool_calls(
        self,
        tool_calls_list: list[dict[str, Any]],
        *,
        seen_fps: set[str],
        result_cache: dict[str, str],
    ) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        """Run tools in parallel (capped waves); always yield one tool msg per call id."""
        from remedy.core.agent_tool_batch import execute_tool_calls

        async for item in execute_tool_calls(
            self,
            tool_calls_list,
            seen_fps=seen_fps,
            result_cache=result_cache,
        ):
            yield item

    async def _call_llm_stream(
        self,
        message: str,
        session_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        *,
        plan_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Call the LLM with a smooth ReAct loop."""
        from remedy.core.agent_react_loop import call_llm_stream

        async for chunk in call_llm_stream(
            self,
            message,
            session_id=session_id,
            attachments=attachments,
            plan_mode=plan_mode,
        ):
            yield chunk

    async def _post_chat(
        self, body: dict[str, Any]
    ) -> dict[str, Any] | str:
        from remedy.core.agent_llm import post_chat

        return await post_chat(self, body)

    async def _apply_session_workspace(self, session_id: str | None) -> None:
        """Bind tools/cwd to the **session** project for this turn."""
        from remedy.core.agent_session import apply_session_workspace

        await apply_session_workspace(self, session_id)

    async def stream_response(
        self,
        message: str,
        session_id: str | None = None,
        model: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        *,
        plan_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Stream tokens from the LLM for real-time SSE delivery.

        Yields individual tokens as they arrive from the provider.
        Tool-call lifecycle events are prefixed with '@@'.
        Falls back to the echo-style fallback when no API key is configured.

        *plan_mode*: restrict tools to planning helpers (plan_save, goals, memory search).
        """
        await self._apply_session_workspace(session_id)

        prev_model = self._llm_model
        if model and str(model).strip():
            self._llm_model = str(model).strip()

        # Fresh per-turn tool trace for auto-learn + checkpoints
        self._turn_tool_steps = []
        self._last_auto_checkpoint_n = 0
        self._plan_mode = bool(plan_mode)
        if session_id:
            self._session_id = session_id

        try:
            if not self._llm_api_key:
                yield (
                    "[LLM not connected — no API key. "
                    "Open Settings, enter your provider key, Save, then resend.]\n"
                )
                return

            async for chunk in self._call_llm_stream(
                message,
                session_id=session_id,
                attachments=attachments,
                plan_mode=bool(plan_mode),
            ):
                yield chunk
        finally:
            self._llm_model = prev_model
            self._plan_mode = False
            # Soft end-of-turn checkpoint if substantial tool work happened
            if not plan_mode:
                with suppress(Exception):
                    steps = list(getattr(self, "_turn_tool_steps", None) or [])
                    if len(steps) >= 4:
                        self._maybe_auto_checkpoint(
                            reason="turn_end",
                            title=(message or "Task")[:80],
                            force=True,
                        )
                # Post-turn: distill multi-step successes into probation skills
                with suppress(Exception):
                    self._maybe_auto_learn_from_turn(message, session_id)

    def _maybe_auto_learn_from_turn(
        self,
        message: str,
        session_id: str | None,
    ) -> None:
        """If this turn used enough successful tools, learn a probation skill."""
        from remedy.core.agent_learn import auto_learn_from_turn

        auto_learn_from_turn(
            learning_loop=self._get_learning_loop(),
            message=message,
            session_id=session_id,
            steps=list(getattr(self, "_turn_tool_steps", None) or []),
        )

    def _maybe_auto_checkpoint(
        self,
        *,
        reason: str,
        title: str = "",
        force: bool = False,
    ) -> str | None:
        """Snapshot mid-turn progress for long Build runs. Returns markdown or None."""
        if getattr(self, "_plan_mode", False):
            return None
        steps = list(getattr(self, "_turn_tool_steps", None) or [])
        if len(steps) < 2 and not force:
            return None
        from remedy.core.checkpoint import (
            AUTO_CHECKPOINT_EVERY_N_STEPS,
            CheckpointStore,
            build_checkpoint_from_tool_steps,
        )

        n = len(steps)
        last_n = int(getattr(self, "_last_auto_checkpoint_n", 0) or 0)
        if not force:
            if n < AUTO_CHECKPOINT_EVERY_N_STEPS:
                return None
            if n - last_n < AUTO_CHECKPOINT_EVERY_N_STEPS:
                return None
        cp = build_checkpoint_from_tool_steps(
            steps,
            session_id=str(getattr(self, "_session_id", "") or "") or None,
            title=title or f"Auto checkpoint ({reason})",
            reason=reason,
        )
        store = CheckpointStore(getattr(self.config, "home_dir", None))
        store.save(cp)
        self._last_auto_checkpoint_n = n
        with suppress(Exception):
            from remedy.memory.harness.brief import SessionBrief

            if self._session_brief is None:
                self._session_brief = SessionBrief()
            self._session_brief.decisions.append(
                f"Checkpoint [{reason}]: {cp.done[-1] if cp.done else n} tools"
            )
            self._session_brief.decisions = self._session_brief.decisions[-20:]
            for nxt in cp.next_steps[:3]:
                if nxt not in self._session_brief.open_tasks:
                    self._session_brief.open_tasks.append(nxt)
            self._session_brief.open_tasks = self._session_brief.open_tasks[-20:]
            self._session_brief.touch()
        return cp.summary_markdown()

    async def _build_context(self) -> str:
        """Assemble turn context (workspace, Partner Memory, brief, skills).

        Implementation lives in :mod:`remedy.core.agent_context` so this
        module stays an orchestrator and context can be typed under mypy.
        """
        from remedy.core.agent_context import build_turn_context

        return await build_turn_context(self)

    def _fallback_response(self, message: str, event: GatewayEvent) -> str:
        from remedy.core.agent_llm import fallback_response

        _ = event
        return fallback_response(self, message)
