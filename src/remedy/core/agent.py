"""Concrete agent runtime -- BasicRuntime with LLM integration and ReAct tool use.

Provides the default Remedy agent: a multi-step ReAct loop that stores conversation
in memory, calls LLM providers through the adapter layer, and invokes tools
through the ToolRegistry.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.errors import SecurityError, format_tool_error
from remedy.core.providers import ProviderAdapter, select_provider
from remedy.core.react_policy import (
    MAX_PARALLEL_TOOLS as _MAX_PARALLEL_TOOLS,
)
from remedy.core.react_policy import (
    MAX_REACT_STEPS as _MAX_REACT_STEPS,
)
from remedy.core.react_policy import (
    REACT_AUTO_CONTINUE as _REACT_AUTO_CONTINUE,
)
from remedy.core.react_policy import (
    REACT_EPOCH_STEPS as _REACT_EPOCH_STEPS,
)
from remedy.core.react_policy import (
    REACT_MAX_STALE_EPOCHS as _REACT_MAX_STALE_EPOCHS,
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
from remedy.core.tool_timeouts import tool_timeout_for
from remedy.core.workspace import (
    allowed_roots_for_scope,
    effective_access_scope,
    ensure_project_dir,
    is_forbidden_project_path,
    is_unset_project_path,
    normalize_access_scope,
    resolve_project_path,
    write_roots_for_scope,
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
    "_REACT_EPOCH_STEPS",
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
        self._system_prompt = _build_system_prompt(
            getattr(config, "persona", None),
            name=getattr(config, "name", None),
            gender=getattr(config, "agent_gender", None),
            ui_language=getattr(config, "ui_language", None),
        )
        self._llm_api_key: str = config.llm_api_key
        self._llm_model: str = config.llm_model
        self._llm_base_url: str = config.llm_base_url or "https://api.openai.com/v1"
        self._llm_provider: str = getattr(config, "llm_provider", "openai") or "openai"
        # Sleev gateway flags (hot-reloaded from Settings; read by prepare_llm_http).
        self._sleev_enabled: bool = bool(getattr(config, "sleev_enabled", False))
        self._sleev_gateway_url: str = str(
            getattr(config, "sleev_gateway_url", "") or ""
        ).strip()
        self._sleev_allow_remote_gateway: bool = bool(
            getattr(config, "sleev_allow_remote_gateway", False)
        )
        self._provider: ProviderAdapter = select_provider(
            self._llm_provider, self._llm_base_url
        )
        # Sessions actively streaming (not a single global bool).
        self._streaming_sessions: set[str] = set()
        # Serialize LLM bind + stream: one shared provider binding per process.
        self._llm_turn_lock: asyncio.Lock | None = None
        # Absolute safety total (multi-epoch). Soft epoch size is separate.
        self._max_react_steps = _MAX_REACT_STEPS
        self._epoch_react_steps = _REACT_EPOCH_STEPS
        self._react_auto_continue = _REACT_AUTO_CONTINUE
        self._react_max_stale_epochs = _REACT_MAX_STALE_EPOCHS
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
        # RMB local agent: always use earlier thresholds (config cloud defaults 0.75/0.92
        # must not override when chat is RMB).
        _min_def, _max_def = 0.75, 0.92
        _rmb_agent = False
        with suppress(Exception):
            from remedy.runtime.rmb.mode import (
                harness_pcts_for_local_agent,
                is_local_agent_mode,
                is_rmb_provider,
            )

            _prov = str(getattr(config, "llm_provider", "") or "")
            _url = str(getattr(config, "llm_base_url", "") or "")
            _rmb_agent = is_local_agent_mode(
                {"llm_provider": _prov, "llm_base_url": _url}
            ) or is_rmb_provider(_prov, _url)
            if _rmb_agent:
                _min_def, _max_def = harness_pcts_for_local_agent()
        if _rmb_agent:
            self._harness_min_pct = float(_min_def)
            self._harness_max_pct = float(_max_def)
        else:
            self._harness_min_pct = float(
                getattr(config, "harness_min_context_pct", None) or _min_def
            )
            self._harness_max_pct = float(
                getattr(config, "harness_max_context_pct", None) or _max_def
            )
        # Default high — medium/off were throttling max_tokens and truncating reasoning.
        tl = str(getattr(config, "thinking_level", None) or "high").strip().lower()
        # Local/RMB: never default to high thinking (monologue tax)
        with suppress(Exception):
            prov = str(getattr(config, "llm_provider", None) or "").lower()
            if prov in ("rmb", "llamacpp", "ollama", "local"):
                tl = "low"
        self._thinking_level: str = (
            tl if tl in ("off", "low", "medium", "high") else "high"
        )
        from remedy.core.approvals import normalize_approval_mode

        self._approval_mode: str = normalize_approval_mode(
            getattr(config, "approval_mode", None)
        )
        with suppress(Exception):
            from remedy.core.approvals import APPROVALS

            APPROVALS.set_mode(self._approval_mode)
        # Memory Harness L2 working state — per-session map + current-turn mirror.
        # Live stores sit behind properties so concurrent stream turns prefer
        # turn ContextVars (see turn_context.begin_turn).
        self.__dict__["_session_id_live"] = getattr(self, "_session_id", None)
        self.__dict__["_session_brief_live"] = None
        self.__dict__["_partner_state_live"] = None
        self.__dict__["_work_roots_live"] = []
        self._session_briefs: dict[str, Any] = {}
        self._learning_loop: Any | None = None
        self._register_workspace_tools()
        self._register_memory_tools()

    # -- turn-local continuity (properties) ---------------------------------

    @property
    def _session_id(self) -> str | None:
        try:
            from remedy.core.turn_context import current_session_id, in_active_turn

            if in_active_turn():
                return current_session_id()
        except Exception:
            pass
        return self.__dict__.get("_session_id_live")

    @_session_id.setter
    def _session_id(self, value: str | None) -> None:
        self.__dict__["_session_id_live"] = value

    @property
    def _session_brief(self) -> Any:
        try:
            from remedy.core.turn_context import in_active_turn, turn_session_brief

            if in_active_turn():
                return turn_session_brief()
        except Exception:
            pass
        return self.__dict__.get("_session_brief_live")

    @_session_brief.setter
    def _session_brief(self, value: Any) -> None:
        self.__dict__["_session_brief_live"] = value
        try:
            from remedy.core.turn_context import in_active_turn, set_turn_session_brief

            if in_active_turn():
                set_turn_session_brief(value)
        except Exception:
            pass

    @property
    def _partner_state(self) -> Any:
        try:
            from remedy.core.turn_context import in_active_turn, turn_partner_state

            if in_active_turn():
                return turn_partner_state()
        except Exception:
            pass
        return self.__dict__.get("_partner_state_live")

    @_partner_state.setter
    def _partner_state(self, value: Any) -> None:
        self.__dict__["_partner_state_live"] = value
        try:
            from remedy.core.turn_context import in_active_turn, set_turn_partner_state

            if in_active_turn():
                set_turn_partner_state(value)
        except Exception:
            pass

    @property
    def _work_roots(self) -> list[str]:
        try:
            from remedy.core.turn_context import in_active_turn, turn_work_roots

            if in_active_turn():
                return turn_work_roots()
        except Exception:
            pass
        return list(self.__dict__.get("_work_roots_live") or [])

    @_work_roots.setter
    def _work_roots(self, value: list[str] | None) -> None:
        roots = list(value or [])
        self.__dict__["_work_roots_live"] = roots
        try:
            from remedy.core.turn_context import in_active_turn, set_turn_work_roots

            if in_active_turn():
                set_turn_work_roots(roots)
        except Exception:
            pass

    def project_path_is_unset(self) -> bool:
        """True when no real project folder is configured (→ full access).

        Prefers per-turn ContextVar so concurrent streams do not steal each
        other's project binding when checking the write jail.
        """
        try:
            from remedy.core.turn_context import current_turn_workspace

            ws = current_turn_workspace()
            if ws is not None:
                return is_unset_project_path(ws.project_raw)
        except Exception:
            pass
        return is_unset_project_path(getattr(self, "_project_path_raw", None))

    def effective_project_path(self) -> Path:
        """Active workspace root for tools / context (session or default).

        Prefers per-turn ContextVar when concurrent streams share this runtime.
        """
        try:
            from remedy.core.turn_context import current_turn_workspace

            ws = current_turn_workspace()
            if ws is not None and ws.active_path:
                return ensure_project_dir(Path(ws.active_path))
        except Exception:
            pass
        try:
            return ensure_project_dir(self._active_project_path)
        except Exception:
            return resolve_project_path(None)

    def access_scope(self) -> str:
        """Configured scope, or **full** when no project folder is set."""
        raw = getattr(self, "_project_path_raw", None)
        try:
            from remedy.core.turn_context import current_turn_workspace

            ws = current_turn_workspace()
            if ws is not None:
                raw = ws.project_raw
        except Exception:
            pass
        return effective_access_scope(
            getattr(self, "_access_scope", None),
            raw,
        )

    def allowed_roots(self) -> list[Path]:
        """Read/research roots (may include Desktop/Documents/Downloads)."""
        return allowed_roots_for_scope(
            self.access_scope(), self.effective_project_path()
        )

    def write_roots(self) -> list[Path]:
        """Mutation roots — follow the owner's access_scope (project|home|full)."""
        return write_roots_for_scope(
            self.access_scope(), self.effective_project_path()
        )

    def _effective_write_approval(self) -> str:
        """Approval mode that governs this turn's writes.

        In-flight turns keep the mode from begin_turn. A mid-turn Settings
        switch to Full must not lift this turn's jail.
        """
        from remedy.core.approvals import APPROVALS, normalize_approval_mode

        try:
            from remedy.core.turn_context import current_turn_approval_mode

            snapped = current_turn_approval_mode()
        except Exception:
            snapped = None
        if snapped:
            return normalize_approval_mode(snapped)
        approval = normalize_approval_mode(APPROVALS.mode)
        if approval != "full":
            approval = normalize_approval_mode(
                getattr(self, "_approval_mode", None)
            )
        return approval

    def resolve_tool_path(self, path: str, *, for_write: bool = False) -> Path:
        """Resolve a tool path: reads are never jailed; writes follow write roots.

        Contract (see ``shell_write_jail`` module docstring): reads resolve
        anywhere the OS user can open (except ``untrusted`` scope); writes /
        edits / shell cwd for mutations pass ``for_write=True`` and are jailed
        to :meth:`write_roots` — project → project only, home → project +
        home, full (or Approvals → Full, or no project bound) → machine-wide.
        Auth secrets and Remedy's own installed code are refused always.
        """
        from remedy.core.workspace import resolve_read_path, resolve_write_path

        scope = self.access_scope()
        if for_write:
            roots = self.write_roots() or [self.effective_project_path()]
            return resolve_write_path(
                path or ".",
                roots=roots,
                access_scope=scope,
                approval_mode=self._effective_write_approval(),
                project_bound=not self.project_path_is_unset(),
            )
        return resolve_read_path(
            path or ".",
            roots=self.allowed_roots(),
            access_scope=scope,
        )

    def set_project_path(self, path: str | Path | None, *, as_default: bool = False) -> Path:
        """Set active (and optionally default) project workspace."""
        raw = None if path is None else str(path)
        if is_unset_project_path(raw):
            self._project_path_raw = None
            resolved = resolve_project_path(None)
        else:
            resolved = resolve_project_path(
                raw,
                fallback=self._default_project_path,
            )
            if is_forbidden_project_path(raw) or is_forbidden_project_path(resolved):
                raise SecurityError(
                    f"Project path is not allowed: {resolved}. "
                    "Pick a user folder, not an OS or program directory.",
                    rule="forbidden_project",
                    path=str(resolved),
                )
            self._project_path_raw = raw.strip() if raw else None
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
        # Non-blocking: ensure pinned ripgrep under ~/.remedy/bin when missing
        with suppress(Exception):
            from remedy.core.rg_binary import schedule_ensure_rg

            home = getattr(self.config, "home_dir", None)
            schedule_ensure_rg(home)

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
        agent_gender: str | None = None,
        project_path: str | None = None,
        access_scope: str | None = None,
        harness_mode: str | None = None,
        harness_min_context_pct: float | None = None,
        harness_max_context_pct: float | None = None,
        thinking_level: str | None = None,
        approval_mode: str | None = None,
        ui_language: str | None = None,
    ) -> None:
        """Hot-apply LLM / persona / identity / project settings without restarting."""
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
        _provider_switched = False
        old_provider = getattr(self, "_llm_provider", None)
        old_model = getattr(self, "_llm_model", None)
        if provider is not None and provider.strip():
            new_p = provider.strip().lower()
            _provider_switched = new_p != old_provider
            _prov_changed = _provider_switched
            self._llm_provider = new_p
            self._provider = select_provider(self._llm_provider, self._llm_base_url)
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
            self._provider = select_provider(self._llm_provider, self._llm_base_url)
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    self.config.llm_base_url = self._llm_base_url
        # Retune harness when provider *or* base_url changes effective local/RMB mode
        _url_changed = base_url is not None and bool(str(base_url).strip())
        if _prov_changed or _url_changed or harness_min_context_pct is not None:
            with suppress(Exception):
                from remedy.runtime.rmb.mode import (
                    harness_pcts_for_local_agent,
                    is_rmb_provider,
                )

                if is_rmb_provider(
                    getattr(self, "_llm_provider", None),
                    getattr(self, "_llm_base_url", None),
                ):
                    # Explicit reconfigure values win when provided; else RMB defaults
                    if harness_min_context_pct is None:
                        lo, hi = harness_pcts_for_local_agent()
                        self._harness_min_pct = lo
                        self._harness_max_pct = hi
                    # Keep endless-context budget on the live configured n_ctx
                    with suppress(Exception):
                        from remedy.runtime.rmb.config import load_rmb_json, merge_state
                        from remedy.runtime.rmb.service import sync_context_window_cache

                        sync_context_window_cache(merge_state(load_rmb_json()))
                elif harness_min_context_pct is None and harness_max_context_pct is None:
                    self._harness_min_pct = float(
                        getattr(self.config, "harness_min_context_pct", None) or 0.75
                    )
                    self._harness_max_pct = float(
                        getattr(self.config, "harness_max_context_pct", None) or 0.92
                    )
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
        if _provider_switched:
            leftover = str(getattr(self, "_llm_api_key", "") or "").strip().lower()
            new_p = str(getattr(self, "_llm_provider", "") or "").strip().lower()
            if leftover in ("local", "rmb", "unused") and new_p not in (
                "ollama",
                "rmb",
                "llamacpp",
                "custom",
                "local",
            ):
                self._llm_api_key = ""
                if hasattr(self, "config") and self.config is not None:
                    with suppress(Exception):
                        self.config.llm_api_key = ""
        if persona is not None:
            p = persona.strip().lower() if persona.strip() else "default"
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    self.config.persona = p
        if name is not None and name.strip():
            if hasattr(self, "config") and self.config is not None:
                with suppress(Exception):
                    self.config.name = name.strip()
        if agent_gender is not None:
            with suppress(Exception):
                from remedy.core.agent_identity import normalize_agent_gender

                g_set = normalize_agent_gender(agent_gender)
                if hasattr(self, "config") and self.config is not None:
                    self.config.agent_gender = g_set
        if ui_language is not None:
            with suppress(Exception):
                from remedy.i18n.languages import normalize_ui_language

                lang = normalize_ui_language(ui_language)
                if hasattr(self, "config") and self.config is not None:
                    self.config.ui_language = lang
        # Rebuild system prompt when persona/name/gender/language change
        if (
            persona is not None
            or (name is not None and str(name).strip())
            or agent_gender is not None
            or ui_language is not None
        ):
            with suppress(Exception):
                from remedy.core.agent_identity import (
                    normalize_agent_gender,
                    normalize_agent_name,
                    sync_identity_to_soul,
                )

                cfg = getattr(self, "config", None)
                n = normalize_agent_name(
                    name if name is not None else getattr(cfg, "name", None)
                )
                g = normalize_agent_gender(
                    agent_gender
                    if agent_gender is not None
                    else getattr(cfg, "agent_gender", None)
                )
                p = (
                    (persona.strip().lower() if persona and persona.strip() else None)
                    or getattr(cfg, "persona", None)
                    or "default"
                )
                reply_lang = getattr(cfg, "ui_language", None)
                self._system_prompt = _build_system_prompt(
                    p, name=n, gender=g, ui_language=reply_lang
                )
                home = getattr(cfg, "home_dir", None)
                sync_identity_to_soul(n, g, home=home)
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
        # Plan mode: refuse mutating tools even if they slipped into the schema.
        # Prefer per-turn ContextVar so concurrent Build + Plan tabs do not race.
        from remedy.core.turn_context import current_plan_mode

        if current_plan_mode(self):
            from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES

            if name not in PLAN_MODE_TOOL_NAMES:
                if str(name).startswith("computer_"):
                    suggestion = (
                        "Switch to Build mode (Ctrl+B) for click/type/scroll/act. "
                        "Plan mode allows computer observe tools only "
                        "(snapshot, screenshot, navigate, monitors, page_text, find, wait)."
                    )
                else:
                    suggestion = (
                        "Switch to Build mode (Ctrl+B) to run shell/file tools."
                    )
                return ToolResult(
                    call_id=tool_call.id,
                    success=False,
                    error=format_tool_error(
                        f"Tool '{name}' blocked in Plan mode",
                        code="PLAN_MODE_BLOCKED",
                        tool_name=name,
                        suggestion=suggestion,
                    ),
                )
        default_registry.counter("remedy_tool_calls_total", tool=name).inc()
        t0 = _time.perf_counter()
        try:
            # First positional must NOT be called "name" — many tools take a
            # `name` argument (skill_activate, skill_run) and would raise
            # TypeError: multiple values for argument 'name'.
            # Per-tool wall clock: a handler that blocks forever (a runaway
            # walk, a hung embedder) used to hang the whole turn. Tools that
            # carry their own, longer timeouts (host_run, builds, jobs) are
            # left alone — see ``tool_timeout_for``.
            _budget = tool_timeout_for(name, self.tool_registry)
            _coro = self.tool_registry.execute(
                tool_name=name,
                **(tool_call.arguments or {}),
            )
            if _budget:
                result = await asyncio.wait_for(_coro, timeout=_budget)
            else:
                result = await _coro
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
        except TimeoutError:
            default_registry.counter("remedy_tool_errors_total", tool=name).inc()
            default_registry.counter("remedy_tool_timeouts_total", tool=name).inc()
            default_registry.histogram(
                "remedy_tool_duration_seconds", tool=name
            ).observe(_time.perf_counter() - t0)
            logger.warning("Tool %s timed out after %.0fs", name, _budget)
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=format_tool_error(
                    f"{name} did not finish within {_budget:.0f}s and was cancelled",
                    code="TOOL_TIMEOUT",
                    tool_name=name,
                    suggestion=(
                        "Narrow the request (smaller path=, fewer results, a "
                        "shorter query) or use a tool with an explicit timeout."
                    ),
                ),
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
        except TypeError as e:
            # Missing path= etc. — soft error, never kill the turn
            default_registry.counter("remedy_tool_errors_total", tool=name).inc()
            default_registry.histogram(
                "remedy_tool_duration_seconds", tool=name
            ).observe(_time.perf_counter() - t0)
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=format_tool_error(
                    str(e),
                    code="TOOL_ARGS",
                    tool_name=name,
                    suggestion=(
                        "Pass required args (file_write needs path= and content=; "
                        "file_edit needs path=, old_string=, new_string=)."
                    ),
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

    def _right_size_max_tokens(self) -> int:
        """Compute the completion budget for the bound provider/model.

        Reasoning must never be truncated mid-stream: request the provider's
        accepted maximum (DeepSeek legacy caps at 8k, V4 at 64k, OpenAI-compat
        at 128k). Stored on the runtime so the request builder passes it
        explicitly; auto-continue still covers true finish_reason=length walls.
        """
        from remedy.core.providers import MAX_OUTPUT_TOKENS

        cap = MAX_OUTPUT_TOKENS
        try:
            from remedy.core.llm_binding import get_llm_binding

            bind = get_llm_binding(self)
            adapter = bind.adapter()
            model = bind.model or self._llm_model
            with suppress(Exception):
                cap = int(adapter.provider_max_output_tokens(model))
        except Exception:
            with suppress(Exception):
                adapter = getattr(self, "_provider", None)
                if adapter is not None:
                    cap = int(adapter.provider_max_output_tokens(self._llm_model))
        cap = max(1, cap)
        self._llm_max_output_tokens = cap
        return cap

    async def _call_llm_stream(
        self,
        message: str,
        session_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        *,
        plan_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Call the LLM with a smooth ReAct loop.

        Right-sizes max_tokens up front (reasoning is never truncated
        mid-stream), then runs the loop with ONE retry-with-backoff on
        transient failures before a clean turn-end.
        """
        from remedy.core.agent_react_loop import call_llm_stream

        self._right_size_max_tokens()
        attempt = 0
        while True:
            attempt += 1
            try:
                async for chunk in call_llm_stream(
                    self,
                    message,
                    session_id=session_id,
                    attachments=attachments,
                    plan_mode=plan_mode,
                ):
                    yield chunk
                return
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                if attempt >= 2:
                    # Clean turn-end: no traceback, no dangling stream.
                    logger.warning(
                        "LLM stream failed after 2 attempts — clean turn-end: %s",
                        exc,
                    )
                    yield (
                        "\n*(The model request failed after one retry — "
                        "history is intact. Resend or say **continue**.)*\n"
                    )
                    return
                logger.warning(
                    "LLM stream transient failure (attempt %d) — retrying "
                    "with backoff: %s",
                    attempt,
                    exc,
                )
                yield "@@status:Connection blip — retrying once…\n"
                await asyncio.sleep(1.5 * attempt)

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
        chat_mode: bool = False,
        provider: str | None = None,
        internal: bool = False,
        project_override: str | None = None,
    ) -> AsyncIterator[str]:
        """Stream tokens from the LLM for real-time SSE delivery.

        Yields individual tokens as they arrive from the provider.
        Tool-call lifecycle events are prefixed with '@@'.
        Falls back to the echo-style fallback when no API key is configured.

        *plan_mode*: restrict tools to planning helpers (plan_save, goals, memory search).
        *chat_mode*: pin conversation — no tool pack this turn.
        *provider* / *model*: per-session bind via ContextVar (parallel multi-provider).
        *internal*: unattended self-fix — does not count as a user turn.
        *project_override*: bind the tool jail to this path after session workspace.
        """
        if not internal:
            with suppress(Exception):
                from remedy.core.self_inject import note_user_activity

                note_user_activity()
        from remedy.core.llm_binding import (
            get_llm_binding,
            reset_llm_binding,
            set_llm_binding,
        )
        from remedy.core.turn_context import (
            begin_turn,
            current_turn_tool_steps,
            end_turn,
            is_turn_aborted,
        )

        # Short lock: session project jail + LLM credentials must snapshot
        # atomically so concurrent DeepSeek/Grok streams cannot thrash
        # ``_project_path_raw`` before ``begin_turn`` freezes ContextVars.
        if self._llm_turn_lock is None:
            self._llm_turn_lock = asyncio.Lock()
        lock = self._llm_turn_lock

        async with lock:
            await self._apply_session_workspace(session_id)
            if project_override:
                with suppress(Exception):
                    self.set_project_path(str(project_override), as_default=False)
            # Capture under lock — concurrent set_project_path cannot race us.
            turn_project_raw = getattr(self, "_project_path_raw", None)
            turn_active_path = getattr(self, "_active_project_path", None) or ""
            # Freeze continuity objects for this turn (sibling tabs rebind live
            # mirrors under the same lock; ContextVars keep our stream isolated).
            turn_brief = self.__dict__.get("_session_brief_live")
            turn_partner = self.__dict__.get("_partner_state_live")
            turn_roots = list(self.__dict__.get("_work_roots_live") or [])

            prov = (provider or "").strip() or None
            mod = (model or "").strip() or None
            if not prov and not mod and session_id and self.memory is not None:
                with suppress(Exception):
                    sess = await self.memory.get_chat_session(str(session_id))
                    if sess is not None:
                        prov = (getattr(sess, "llm_provider", None) or None)
                        if prov:
                            prov = str(prov).strip() or None
                        if not mod:
                            mod = (getattr(sess, "model", None) or None)
                            if mod:
                                mod = str(mod).strip() or None
            if not prov and not mod:
                # Unbound session: copy Settings default into this turn only.
                from remedy.interfaces.api_support import resolve_llm_slot

                prov, mod, _, _ = resolve_llm_slot(runtime=self)
            from remedy.interfaces.api_support import binding_for_session

            bind = binding_for_session(prov, mod, runtime=self)

        llm_tok = set_llm_binding(bind)
        self._last_auto_checkpoint_n = 0
        # Per-turn build green flag (set by auto-verify; drives short summary path)
        self._build_verify_green = False
        sid_key = str(session_id or "").strip() or "_anon"
        if session_id:
            self._session_id = session_id

        turn_tokens = begin_turn(
            session_id,
            project_raw=turn_project_raw,
            active_path=turn_active_path,
            plan_mode=bool(plan_mode) and not bool(chat_mode),
            chat_mode=bool(chat_mode),
            session_brief=turn_brief,
            partner_state=turn_partner,
            work_roots=turn_roots,
        )
        with suppress(Exception):
            from remedy.core.turn_pipeline import snapshot_live_turn

            snapshot_live_turn(self)
        # Legacy mirrors for code that still reads runtime attrs (prefer ContextVar).
        self._plan_mode = bool(plan_mode) and not bool(chat_mode)
        self._chat_mode = bool(chat_mode)
        self._turn_tool_steps = current_turn_tool_steps(self)
        self._streaming_sessions.add(sid_key)
        with suppress(Exception):
            home = Path(str(getattr(self.config, "home_dir", "") or "")).expanduser()
            if home.as_posix() not in ("", "."):
                # Per-process lock file with heartbeat: a sibling process on the
                # same home (gateway runner) cannot unlink ours, and a crashed
                # process leaves only a stale file readers ignore.
                from remedy.core.stream_lock import acquire_stream_lock

                acquire_stream_lock(home, sid_key)
        try:
            # L0 local replies work without a provider key (version/whoami/skills/status)
            if not plan_mode and not attachments:
                with suppress(Exception):
                    from remedy.core.metabolism.l0 import try_l0_system_reply
                    from remedy.core.metabolism.tier import TurnTier, classify_turn_tier

                    if (
                        classify_turn_tier(message or "", tools_enabled=False)
                        == TurnTier.L0_INSTANT
                    ):
                        l0 = try_l0_system_reply(
                            self, message or "", preclassified=True
                        )
                        if l0:
                            yield l0
                            return

            if not get_llm_binding(self).api_key:
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
                if is_turn_aborted():
                    yield "@@aborted\n"
                    break
                yield chunk
        finally:
            self._streaming_sessions.discard(sid_key)
            with suppress(Exception):
                home = Path(str(getattr(self.config, "home_dir", "") or "")).expanduser()
                if home.as_posix() not in ("", "."):
                    from remedy.core.stream_lock import release_stream_lock

                    release_stream_lock(home, sid_key)
            steps_snap = list(current_turn_tool_steps(self))
            # Captured before end_turn() resets the turn contextvars.
            aborted_snap = is_turn_aborted()
            # Per-partner intent learning: a turn that really used tools is a
            # confirmed work label for this message shape (see intent_learn).
            if steps_snap and not aborted_snap and not plan_mode and not internal:
                with suppress(Exception):
                    from remedy.core.intent_learn import record_confirmed_work

                    record_confirmed_work(message or "")
            # Write turn-local continuity back into session caches / live store
            # so the next turn for this session resumes the same brief/partner.
            with suppress(Exception):
                from remedy.core.session_continuity import (
                    _brief_by_session,
                    _partner_by_session,
                    _work_roots_by_session,
                )
                from remedy.core.turn_context import (
                    turn_partner_state,
                    turn_session_brief,
                    turn_work_roots,
                )

                sid_end = str(session_id or "").strip()
                brief_end = turn_session_brief(self)
                partner_end = turn_partner_state(self)
                roots_end = turn_work_roots(self)
                if sid_end:
                    if brief_end is not None:
                        _brief_by_session[sid_end] = brief_end
                    if partner_end is not None:
                        _partner_by_session[sid_end] = partner_end
                    _work_roots_by_session[sid_end] = list(roots_end or [])
                # Only refresh live mirrors when no other stream owns a different tab
                live_sid = str(self.__dict__.get("_session_id_live") or "").strip()
                if not live_sid or live_sid == sid_end:
                    self.__dict__["_session_brief_live"] = brief_end
                    self.__dict__["_partner_state_live"] = partner_end
                    self.__dict__["_work_roots_live"] = list(roots_end or [])
            end_turn(session_id, *turn_tokens)
            with suppress(Exception):
                reset_llm_binding(llm_tok)
            self._plan_mode = False
            self._chat_mode = False
            self._turn_tool_steps = []
            # Soft end-of-turn checkpoint if substantial tool work happened
            if not plan_mode and not internal and not is_turn_aborted():
                with suppress(Exception):
                    if len(steps_snap) >= 4:
                        # Temporarily expose steps for checkpoint helper
                        self._turn_tool_steps = steps_snap
                        self._maybe_auto_checkpoint(
                            reason="turn_end",
                            title=(message or "Task")[:80],
                            force=True,
                        )
                with suppress(Exception):
                    self._turn_tool_steps = steps_snap
                    self._maybe_auto_learn_from_turn(message, session_id)
                self._turn_tool_steps = []
            # Closed loop: grade the skills this turn used (auto-suggested or
            # skill_activate'd) by how the turn went. Runs even when aborted so
            # an interrupted turn counts as a failure for the skill on probation.
            if not plan_mode and not internal:
                with suppress(Exception):
                    self._record_skill_turn_outcome(
                        steps_snap, session_id, aborted=aborted_snap
                    )
            with suppress(Exception):
                self.__dict__.pop("_turn_auto_suggested_skill", None)
                self.__dict__.pop("_turn_activated_skills", None)

    @property
    def _streaming(self) -> bool:
        """True if any session is mid-stream (back-compat for routes/abort)."""
        return bool(getattr(self, "_streaming_sessions", None))

    @_streaming.setter
    def _streaming(self, value: bool) -> None:
        """Legacy setter: False clears all; True is a no-op without session id."""
        if not value:
            with suppress(Exception):
                self._streaming_sessions.clear()

    def is_session_streaming(self, session_id: str | None) -> bool:
        """True when this session id has an active stream turn."""
        from remedy.core.turn_context import is_session_streaming as _is_sid

        sid = str(session_id or "").strip()
        if not sid:
            return False
        if sid in getattr(self, "_streaming_sessions", set()):
            return True
        return _is_sid(sid)

    def _maybe_auto_learn_from_turn(
        self,
        message: str,
        session_id: str | None,
    ) -> None:
        """If this turn used enough successful tools, learn a probation skill."""
        from remedy.core.agent_learn import auto_learn_from_turn
        from remedy.core.turn_context import current_turn_tool_steps

        auto_learn_from_turn(
            learning_loop=self._get_learning_loop(),
            message=message,
            session_id=session_id,
            steps=list(current_turn_tool_steps(self)),
            # Settings toggle: creation off still lets evaluation run
            allow_creation=bool(getattr(self.config, "allow_skill_creation", True)),
        )

    def _record_skill_turn_outcome(
        self,
        steps: list,
        session_id: str | None,
        *,
        aborted: bool,
    ) -> None:
        """Grade auto-learned skills used this turn; clears the per-turn lists."""
        from remedy.core.agent_learn import record_skill_turn_outcome

        names: list[str] = []
        auto = self.__dict__.pop("_turn_auto_suggested_skill", None)
        if auto:
            names.append(str(auto))
        for n in list(self.__dict__.pop("_turn_activated_skills", None) or []):
            if n and str(n) not in names:
                names.append(str(n))
        if not names:
            return
        record_skill_turn_outcome(
            self._get_learning_loop(),
            skills=names,
            steps=list(steps or []),
            aborted=bool(aborted),
            session_id=str(session_id or ""),
            registry=getattr(self, "skills", None),
        )

    def _maybe_auto_checkpoint(
        self,
        *,
        reason: str,
        title: str = "",
        force: bool = False,
    ) -> str | None:
        """Snapshot mid-turn progress for long Build runs. Returns markdown or None."""
        from remedy.core.turn_context import (
            current_plan_mode,
            current_turn_tool_steps,
            turn_session_id,
        )

        if current_plan_mode(self):
            return None
        steps = list(current_turn_tool_steps(self))
        if len(steps) < 2 and not force:
            return None
        from remedy.core.checkpoint import (
            AUTO_CHECKPOINT_EVERY_N_STEPS,
            CheckpointStore,
            build_checkpoint_from_tool_steps,
        )

        n = len(steps)
        from remedy.core.turn_context import (
            set_turn_last_auto_checkpoint_n,
            turn_last_auto_checkpoint_n,
        )

        last_n = turn_last_auto_checkpoint_n(self)
        if not force:
            if n < AUTO_CHECKPOINT_EVERY_N_STEPS:
                return None
            if n - last_n < AUTO_CHECKPOINT_EVERY_N_STEPS:
                return None
        cp = build_checkpoint_from_tool_steps(
            steps,
            session_id=str(turn_session_id(self) or "") or None,
            title=title or f"Auto checkpoint ({reason})",
            reason=reason,
        )
        store = CheckpointStore(getattr(self.config, "home_dir", None))
        store.save(cp)
        set_turn_last_auto_checkpoint_n(n, self)
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
