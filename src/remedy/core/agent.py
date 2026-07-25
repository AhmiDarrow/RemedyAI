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
import time
from collections.abc import AsyncIterator
from contextlib import suppress
from pathlib import Path
from typing import Any
from uuid import uuid4

import aiohttp

from remedy.core.errors import SecurityError, format_tool_error
from remedy.core.providers import ProviderAdapter, get_provider
from remedy.core.react_policy import (
    HARD_SAFETY_CHARS as _HARD_SAFETY_CHARS,
)
from remedy.core.react_policy import (
    HISTORY_CHAR_BUDGET as _HISTORY_CHAR_BUDGET,
)
from remedy.core.react_policy import (
    HISTORY_MSG_LIMIT as _HISTORY_MSG_LIMIT,
)
from remedy.core.react_policy import (
    HISTORY_MSG_SOFT_TRIM as _HISTORY_MSG_SOFT_TRIM,
)
from remedy.core.react_policy import (
    MAX_PARALLEL_TOOLS as _MAX_PARALLEL_TOOLS,
)
from remedy.core.react_policy import (
    MAX_REACT_STEPS as _MAX_REACT_STEPS,
)
from remedy.core.react_policy import (
    TOOL_RESULT_CHAR_CAP as _TOOL_RESULT_CHAR_CAP,
)
from remedy.core.react_policy import (
    _build_system_prompt,
    _looks_like_pseudo_tools,
    _message_wants_tools,
    _parse_pseudo_tool_calls,
    _tool_call_fingerprint,
    batch_has_tool_errors,
    message_wants_tools,
    recovery_nudge_message,
    strip_tool_markup,
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
from remedy.core.workspace import (
    allowed_roots_for_scope,
    effective_access_scope,
    ensure_project_dir,
    is_unset_project_path,
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
        """Portable discovery for *any* skill/service local deps — no disk thrash."""

        async def local_discover(
            action: str = "scan",
            target: str = "",
        ) -> str:
            """Find local services/binaries skills need on this machine.

            action=scan   → all skill local: specs + built-ins (comfyui, ollama, …)
            action=status → same as scan (alias)
            action=one    → single target id (e.g. comfyui, ollama)
            """
            from remedy.core.local_discover import (
                collect_skill_local_specs,
                discover_all,
                discover_one,
            )

            act = (action or "scan").strip().lower()
            specs = []
            with suppress(Exception):
                reg = getattr(self, "skills", None)
                if reg is not None:
                    specs = collect_skill_local_specs(list(getattr(reg, "skills", []) or []))

            try:
                if act in ("one", "get", "find") and (target or "").strip():
                    result = await asyncio.to_thread(
                        discover_one, str(target).strip(), skill_specs=specs
                    )
                else:
                    result = await asyncio.to_thread(
                        discover_all, skill_specs=specs, include_builtins=True
                    )
                return json.dumps(result, indent=2, default=str)
            except Exception as e:
                return format_tool_error(
                    str(e),
                    code="DISCOVER_ERROR",
                    tool_name="local_discover",
                    suggestion=(
                        "Set env/config for the service (e.g. COMFYUI_URL) or "
                        "~/.remedy/<service>.json — do not list_dir the whole disk."
                    ),
                )

        self.tool_registry.register_builtin_handler(
            "local_discover",
            "Portable discovery of local services/binaries that skills need "
            "(ComfyUI, Ollama, anything declared in skill frontmatter local:). "
            "ALWAYS prefer this over list_dir/bash disk hunts. "
            "action=scan (default) or action=one with target=comfyui|ollama|…",
            local_discover,
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "scan | one (default scan)",
                    },
                    "target": {
                        "type": "string",
                        "description": "Service id when action=one (comfyui, ollama, …)",
                    },
                },
            },
        )

    def _register_comfyui_tools(self) -> None:
        """Local ComfyUI image generation (status / generate + session attach)."""

        async def comfyui(
            action: str = "status",
            prompt: str = "",
            width: int = 512,
            height: int = 512,
            steps: int = 16,
            seed: int | None = None,
            base_url: str = "",
            timeout: float = 300.0,
        ) -> str:
            """Local ComfyUI via HTTP API — never full-disk list_dir.

            action=status   → GET /system_stats (+ install hints if down)
            action=locate   → portable discovery + start hints
            action=generate → txt2img + attach PNG to current session

            If status/locate show no install, follow the bundled comfyui skill
            "From scratch bootstrap" (download portable, models, start, then generate).
            """
            from remedy.tools import comfyui as comfy

            act = (action or "status").strip().lower()
            try:
                if act in ("status", "health", "ping"):
                    info = await asyncio.to_thread(comfy.status, base_url or None)
                    return json.dumps(info, indent=2)

                if act in ("locate", "find", "where", "install"):
                    info = await asyncio.to_thread(comfy.locate)
                    return json.dumps(info, indent=2)

                if act in ("generate", "run", "txt2img", "image"):
                    text = (prompt or "").strip()
                    if not text:
                        return format_tool_error(
                            "prompt is required for action=generate",
                            code="MISSING_PROMPT",
                            tool_name="comfyui",
                            suggestion=(
                                'Call comfyui with action="generate" and a non-empty prompt.'
                            ),
                        )
                    result = await asyncio.to_thread(
                        comfy.generate_image,
                        text,
                        base_url=base_url or None,
                        width=int(width or 512),
                        height=int(height or 512),
                        steps=int(steps or 16),
                        seed=seed,
                        timeout=float(timeout or 300),
                    )
                    paths = result.get("paths") or []
                    if not paths:
                        return (
                            "ComfyUI finished but no images were returned. "
                            f"prompt_id={result.get('prompt_id')}"
                        )
                    # Attach into the active chat session so the UI can show it.
                    sid = getattr(self, "_session_id", None)
                    home = None
                    if getattr(self, "config", None) is not None:
                        home = getattr(self.config, "home_dir", None)
                    blocks: list[str] = [
                        f"ComfyUI generate ok (prompt_id={result.get('prompt_id')}, "
                        f"seed={result.get('seed')}).",
                    ]
                    # Mark for the agent loop to surface immediately to the user.
                    image_blocks: list[str] = []
                    for p in paths[:4]:
                        img_path = Path(p)
                        if sid:
                            meta = await asyncio.to_thread(
                                comfy.attach_image_to_session,
                                str(sid),
                                img_path,
                                home_dir=home,
                            )
                            md = comfy.markdown_for_image(
                                meta, caption=text[:80], embed_data_uri=True
                            )
                            blocks.append(md)
                            image_blocks.append(md)
                            self._track_artifact(str(meta.get("path") or img_path))
                        else:
                            # Still embed for display even without session id
                            meta = {
                                "name": img_path.name,
                                "path": str(img_path),
                                "mime": "image/png",
                                "view_url": "",
                            }
                            md = comfy.markdown_for_image(
                                meta, caption=text[:80], embed_data_uri=True
                            )
                            blocks.append(md)
                            image_blocks.append(md)
                            self._track_artifact(str(img_path))
                    blocks.append(
                        "IMPORTANT: Your next message to the user MUST include the "
                        "markdown image block(s) above exactly (do not invent links)."
                    )
                    # Special marker consumed by the ReAct loop to stream the image
                    # into chat even if the model forgets to paste it.
                    if image_blocks:
                        blocks.append("@@REMEDY_IMAGE_MARKDOWN@@\n" + "\n\n".join(image_blocks))
                    return "\n\n".join(blocks)

                return format_tool_error(
                    f"unknown action: {action}",
                    code="BAD_ACTION",
                    tool_name="comfyui",
                    suggestion='Use action="status", "locate", or "generate".',
                )
            except Exception as e:
                return format_tool_error(
                    str(e),
                    code="COMFY_ERROR",
                    tool_name="comfyui",
                    suggestion=(
                        "Call comfyui action=locate. If no install: follow comfyui "
                        "skill From scratch bootstrap (portable download → models → "
                        "start). If install exists: run start_hint / main.py --listen. "
                        "Do NOT list_dir the whole disk."
                    ),
                )

        self.tool_registry.register_builtin_handler(
            "comfyui",
            "Local ComfyUI on ANY machine (including from-scratch). NEVER list_dir "
            "to hunt installs — use this tool. action=status → probe API; "
            "action=locate → discovery + start hints (empty = bootstrap from skill); "
            "action=generate → txt2img + attach PNG. If nothing is installed, follow "
            "the comfyui skill bootstrap: official portable download, Flux.2 Klein "
            "models, start server, then generate. "
            "Overrides: COMFYUI_URL / COMFYUI_HOME or ~/.remedy/comfyui.json.",
            comfyui,
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "status | locate | generate (default status)",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Image prompt (required for generate)",
                    },
                    "width": {"type": "integer", "description": "Width (default 512)"},
                    "height": {"type": "integer", "description": "Height (default 512)"},
                    "steps": {"type": "integer", "description": "Sampler steps (default 16)"},
                    "seed": {
                        "type": "integer",
                        "description": "Optional seed (random if omitted)",
                    },
                    "base_url": {
                        "type": "string",
                        "description": "Override ComfyUI URL (default http://127.0.0.1:8188)",
                    },
                    "timeout": {
                        "type": "number",
                        "description": "Max wait seconds for generation (default 300)",
                    },
                },
                "required": ["action"],
            },
        )

    def _register_vision_tools(self) -> None:
        """Local visual decoder: status / install / decode image paths."""

        async def vision_decode(
            action: str = "status",
            path: str = "",
            question: str = "",
            prefer_cuda: bool = False,
        ) -> str:
            """Local visual decoder (llama.cpp + Qwen2.5-VL 3B).

            action=status   → install/ready/running + model id
            action=install  → start opt-in download of runtime + model (background)
            action=decode   → describe/OCR an image path into structured text
            """
            from remedy.vision import catalog as vision_catalog
            from remedy.vision.decoder import decode_image
            from remedy.vision.service import (
                ensure_server,
                get_status,
                start_install,
            )

            cfg: dict[str, Any] = {}
            with suppress(Exception):
                from remedy.interfaces.config import load_config

                cfg = load_config() or {}
            if getattr(self, "config", None) is not None:
                home = getattr(self.config, "home_dir", None)
                if home:
                    cfg = {**cfg, "home_dir": home}

            act = (action or "status").strip().lower()
            if act in ("status", "info", "health"):
                st = get_status(cfg)
                return json.dumps(
                    {
                        "enabled": st.get("enabled"),
                        "installed": st.get("installed"),
                        "ready": st.get("ready"),
                        "running": st.get("running"),
                        "force_decode": st.get("force_decode"),
                        "model_id": st.get("model_id"),
                        "model": st.get("model"),
                        "progress": st.get("progress"),
                        "hint": st.get("not_ready_hint"),
                        "default_model_id": vision_catalog.DEFAULT_MODEL_ID,
                    },
                    indent=2,
                )

            if act in ("install", "setup", "download"):
                result = start_install(cfg=cfg, prefer_cuda=bool(prefer_cuda))
                return json.dumps(result, indent=2, default=str)

            if act in ("decode", "describe", "ocr", "read"):
                p = (path or "").strip()
                if not p:
                    return format_tool_error(
                        "path is required for action=decode",
                        code="MISSING_PATH",
                        tool_name="vision_decode",
                        suggestion='vision_decode action="decode" path="C:/path/to/image.png"',
                    )
                img = Path(p)
                if not img.is_file():
                    # Try workspace resolve
                    with suppress(Exception):
                        img = self.resolve_tool_path(p)
                if not img.is_file():
                    return format_tool_error(
                        f"Image not found: {path}",
                        code="FILE_NOT_FOUND",
                        tool_name="vision_decode",
                        suggestion="Use an absolute path or a session attachment path.",
                    )
                st = get_status(cfg)
                if not st.get("ready"):
                    return format_tool_error(
                        st.get("not_ready_hint")
                        or "Visual decoder not ready. Install via Settings or action=install.",
                        code="VISION_NOT_READY",
                        tool_name="vision_decode",
                        suggestion='Call vision_decode action="install" (with user approval) then retry decode.',
                    )
                started = await asyncio.to_thread(ensure_server, cfg)
                if not started.get("ok"):
                    return format_tool_error(
                        started.get("error") or "Failed to start llama-server",
                        code="VISION_START_FAILED",
                        tool_name="vision_decode",
                    )
                base = st.get("base_url") or started.get("base_url")
                result = await asyncio.to_thread(
                    decode_image,
                    img,
                    base_url=str(base),
                    extra_question=(question or "").strip() or None,
                )
                if not result.get("ok"):
                    return format_tool_error(
                        result.get("error") or "decode failed",
                        code="VISION_DECODE_FAILED",
                        tool_name="vision_decode",
                    )
                return str(result.get("text") or "")

            return format_tool_error(
                f"Unknown action={action!r}",
                code="BAD_ACTION",
                tool_name="vision_decode",
                suggestion='Use action="status", "install", or "decode".',
            )

        self.tool_registry.register_builtin_handler(
            "vision_decode",
            "Local visual decoder (Qwen2.5-VL 3B via llama.cpp). "
            "status | install | decode an image path to structured text "
            "(scene, OCR, UI). Use when the chat model cannot see images, "
            "or to re-ask about an attached screenshot.",
            vision_decode,
            {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "description": "status | install | decode (default status)",
                    },
                    "path": {
                        "type": "string",
                        "description": "Image path (required for decode)",
                    },
                    "question": {
                        "type": "string",
                        "description": "Optional focus question for decode",
                    },
                    "prefer_cuda": {
                        "type": "boolean",
                        "description": "For install: prefer CUDA llama-server build",
                    },
                },
            },
        )

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
                # Soft-trim only when explicitly configured (>0). Default 0 = full text.
                if _HISTORY_MSG_SOFT_TRIM > 0 and len(content) > _HISTORY_MSG_SOFT_TRIM:
                    content = content[:_HISTORY_MSG_SOFT_TRIM] + "\n…[truncated]"
            # Prefer dropping older turns over mid-message slicing.
            if len(content) > budget:
                if selected:
                    break
                # Newest message alone exceeds budget — keep full unless soft-trim on.
                if _HISTORY_MSG_SOFT_TRIM > 0:
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
            # Full tool results for the model (cap only if TOOL_RESULT_CHAR_CAP > 0).
            cap = _TOOL_RESULT_CHAR_CAP if _TOOL_RESULT_CHAR_CAP > 0 else _HARD_SAFETY_CHARS
            if len(content_str) > cap:
                content_str = (
                    content_str[:cap]
                    + f"\n…[safety cap {cap} chars — re-run with a narrower query if needed]"
                )
            result_cache[fp] = content_str
            seen_fps.add(fp)
            # Trace step for post-turn auto-learn
            with suppress(Exception):
                steps = getattr(self, "_turn_tool_steps", None)
                if isinstance(steps, list):
                    steps.append(
                        {
                            "tool": name or "unknown",
                            "args": {
                                k: (str(v)[:80] if not isinstance(v, (int, float, bool)) else v)
                                for k, v in list(args.items())[:12]
                            },
                            "success": bool(result.success),
                            "result": (content_str or "")[:200],
                            "error": None if result.success else (result.error or "failed"),
                            "duration_ms": float(getattr(result, "duration_ms", 0) or 0),
                        }
                    )
            # Background continuity: pattern observation + stuck signals
            with suppress(Exception):
                from remedy.core.session_quality import get_session_quality
                from remedy.core.speculative import schedule_speculative_prep
                from remedy.nanoswarm import get_swarm
                from remedy.nanoswarm.events import SwarmEvent

                get_session_quality(
                    str(getattr(self, "_session_id", "") or "")
                ).record_tool_result(success=bool(result.success))
                get_swarm().dispatch(
                    SwarmEvent.tool_step(
                        name or "unknown",
                        success=bool(result.success),
                        duration_ms=float(getattr(result, "duration_ms", 0) or 0),
                        session_id=str(getattr(self, "_session_id", "") or ""),
                    )
                )
                # Speculative prep while more tools / model continue
                schedule_speculative_prep(
                    session_id=str(getattr(self, "_session_id", "") or ""),
                    brief=getattr(self, "_session_brief", None),
                    messages=getattr(self, "_last_send_messages", None),
                    project_path=str(
                        getattr(self.config, "project_path", None)
                        or getattr(self, "_project_path", None)
                        or ""
                    )
                    or None,
                    memory=getattr(self, "memory", None),
                )
            return content_str

        def _progress_marker(
            *,
            label: str,
            step: int | None = None,
            total: int | None = None,
            percent: float | None = None,
            force_percent: bool = False,
        ) -> str:
            """Build @@progress payload for the desktop task progress bar.

            Single long jobs stay indeterminate (no percent) until finished so
            the UI doesn't freeze at 0%. Multi-step batches get real %.
            """
            payload: dict[str, Any] = {"label": label}
            if step is not None:
                payload["step"] = step
            if total is not None:
                payload["total"] = total
            multi = bool(total and total > 1)
            if percent is not None and (force_percent or multi or (step or 0) >= (total or 0) > 0):
                payload["percent"] = round(float(percent), 1)
            elif multi and step is not None and total:
                payload["percent"] = round(100.0 * float(step) / float(total), 1)
            return f"@@progress:{json.dumps(payload, separators=(',', ':'))}"

        # Execute only fingerprints not already cached; never drop remainder past cap.
        to_run = [fp for fp in fp_order if fp not in result_cache]
        total_jobs = max(len(to_run), 1)
        completed_jobs = 0
        if to_run:
            first_name = (
                ((fp_to_tc[to_run[0]].get("function") or {}).get("name") or "tools").strip()
            )
            # Label only while in-flight; avoid a stuck 0% for one long job.
            yield _progress_marker(
                label=first_name if len(to_run) == 1 else f"{len(to_run)} tools",
                step=0,
                total=total_jobs,
            ), {}

        for wave_start in range(0, len(to_run), _MAX_PARALLEL_TOOLS):
            wave = to_run[wave_start : wave_start + _MAX_PARALLEL_TOOLS]
            wave_names: list[str] = []
            for fp in wave:
                tc = fp_to_tc[fp]
                name = ((tc.get("function") or {}).get("name") or "").strip()
                wave_names.append(name or "tool")
                raw_args = (tc.get("function") or {}).get("arguments") or "{}"
                try:
                    args_obj = (
                        json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
                    )
                except Exception:
                    args_obj = {"_raw": str(raw_args)[:2000]}
                if not isinstance(args_obj, dict):
                    args_obj = {"value": args_obj}
                # Structured tool_call for UI process trace (args for full mode).
                yield (
                    "@@tool_call:"
                    + json.dumps(
                        {"name": name or "tool", "args": args_obj},
                        default=str,
                        separators=(",", ":"),
                    )
                ), {}
            label = (
                wave_names[0]
                if len(wave_names) == 1
                else f"{len(wave_names)} tools"
            )
            yield _progress_marker(
                label=label,
                step=completed_jobs,
                total=total_jobs,
            ), {}

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
                completed_jobs += 1
                done = completed_jobs >= total_jobs
                yield _progress_marker(
                    label=name or "tool",
                    step=completed_jobs,
                    total=total_jobs,
                    percent=(100.0 if done else 100.0 * completed_jobs / total_jobs),
                    force_percent=done,
                ), {}

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
            # Surface generated images immediately (don't wait for model to restate).
            if name == "comfyui" and "@@REMEDY_IMAGE_MARKDOWN@@" in content_str:
                marker = "@@REMEDY_IMAGE_MARKDOWN@@"
                img_md = content_str.split(marker, 1)[-1].strip()
                if img_md:
                    yield f"@@image_markdown:{img_md}", {}
                # Keep tool payload for the model without the huge data-URI blob.
                content_str = content_str.split(marker, 1)[0].strip()
                if len(content_str) > 2000:
                    content_str = content_str[:2000] + "\n…[image already sent to user]"
            # Full raw dump for UI process trace (Full mode).
            # Keep a hard ceiling only so multi‑MB binary dumps cannot freeze SSE.
            _UI_TRACE_CAP = 500_000
            preview = content_str
            if len(preview) > _UI_TRACE_CAP:
                preview = (
                    preview[:_UI_TRACE_CAP]
                    + f"\n…[{len(content_str)} chars total — UI safety cap]"
                )
            ok = not (
                '"code": "TOOL_' in content_str
                or content_str.startswith("Error")
                or "TOOL_EXCEPTION" in content_str
            )
            yield (
                "@@tool_result:"
                + json.dumps(
                    {
                        "name": name or "unknown",
                        "preview": preview,
                        "ok": ok,
                    },
                    default=str,
                    separators=(",", ":"),
                )
            ), {
                "role": "tool",
                "tool_call_id": call_id,
                "content": content_str,
            }

    async def _call_llm_stream(
        self,
        message: str,
        session_id: str | None = None,
        attachments: list[dict[str, Any]] | None = None,
        *,
        plan_mode: bool = False,
    ) -> AsyncIterator[str]:
        """Call the LLM with a smooth ReAct loop (OpenCode-grade).

        Yields status tokens prefixed with '@@' for tool-call lifecycle events.
        Never leaves the user with a bare "tool limit" dead-end — final step
        always forces a plain-text answer (or a short synthesis).

        When *plan_mode* is True, only planning tools run (no shell/file writes).
        """
        try:
            from remedy.interfaces.attachments import build_multimodal_user_content

            # For Partner Memory ranking + quiet distillation hooks
            with suppress(Exception):
                self._last_user_text = (message or "")[:4000]
            context = await self._build_context()
            # Surface active plan + plan-mode instructions
            with suppress(Exception):
                from pathlib import Path

                from remedy.core.plan_store import PlanStore

                home = getattr(self.config, "home_dir", None) or (Path.home() / ".remedy")
                store = PlanStore(home)
                plan = store.latest_for_session(session_id)
                if plan is not None:
                    context = (
                        (context or "")
                        + "\n\n## Active task plan\n"
                        + plan.summary_markdown()
                    )
                if plan_mode:
                    context = (
                        (context or "")
                        + "\n\n## Plan mode (active)\n"
                        "You are exploring and planning — do **not** edit files, run shell, "
                        "or mutate the system. Use plan_save to store a structured plan with "
                        "clear steps and risks, then summarize for the user. "
                        "They will switch to Build mode to execute."
                    )
            history = await self._load_session_history(session_id, message)
            # Memory Harness L0: prune send-view only (stored transcript untouched)
            with suppress(Exception):
                from remedy.memory.harness.pruner import prune_messages_for_send

                if self._harness_mode != "off":
                    # max_tool_chars=0 → no content shortening (dedupe only).
                    history = prune_messages_for_send(
                        history,
                        max_tool_chars=_TOOL_RESULT_CHAR_CAP,
                        dedupe_tools=True,
                    )
            # Visual decoder path for text-only chat models + image attachments.
            vision_mode = "native"
            decode_brief: str | None = None
            with suppress(Exception):
                from remedy.vision.service import decode_for_turn

                cfg_for_vision: dict[str, Any] = {}
                with suppress(Exception):
                    from remedy.interfaces.config import load_config

                    cfg_for_vision = load_config() or {}
                vres = decode_for_turn(
                    attachments,
                    provider=self._llm_provider,
                    model=self._llm_model,
                    cfg=cfg_for_vision,
                )
                mode = str(vres.get("mode") or "native")
                if mode == "decode" and vres.get("combined"):
                    vision_mode = "decode"
                    decode_brief = str(vres.get("combined") or "")
                    for ev in vres.get("events") or []:
                        yield f"@@status:{ev}\n"
                elif mode == "unavailable" and vres.get("hint"):
                    # Inject hint text (no image_url) so text-only models stay safe
                    vision_mode = "decode"
                    decode_brief = (
                        f"[Visual decoder unavailable] {vres.get('hint')}\n"
                        "Image files are attached by path only."
                    )
                    yield (
                        "@@status:Visual decoder unavailable — "
                        "enable in Settings for local image understanding\n"
                    )

            user_content = build_multimodal_user_content(
                message,
                attachments,
                vision_mode=vision_mode,
                decode_brief=decode_brief,
            )
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
            # Continuity layer: single ContextSnapshot (tokens, policy, remedies, brief)
            with suppress(Exception):
                if self._harness_mode == "auto":
                    from remedy.core.context_snapshot import build_context_snapshot
                    from remedy.memory.harness.compressor import (
                        compression_nudge_message,
                        estimate_tokens,
                        heuristic_merge_from_history,
                    )
                    from remedy.memory.harness.pruner import prune_messages_for_send

                    provider = str(
                        getattr(self.config, "provider", None)
                        or getattr(self.config, "llm_provider", "")
                        or ""
                    )
                    model = str(
                        getattr(self.config, "model", None)
                        or getattr(self.config, "llm_model", "")
                        or ""
                    )
                    project_path = str(
                        getattr(self.config, "project_path", None)
                        or getattr(self, "_project_path", None)
                        or ""
                    ) or None
                    sid = str(getattr(self, "_session_id", "") or "")
                    snap = build_context_snapshot(
                        messages=messages,
                        user_text=message or "",
                        brief=getattr(self, "_session_brief", None),
                        session_id=sid,
                        provider=provider or None,
                        model=model or None,
                        min_pct=self._harness_min_pct,
                        max_pct=self._harness_max_pct,
                        project_path=project_path,
                    )
                    self._last_context_snapshot = snap
                    self._last_send_messages = list(messages)
                    est = snap.token_estimate
                    level = snap.nudge

                    # Inject policy + quality remedies + project pins as system notes
                    injects: list[str] = []
                    if snap.policy_system:
                        injects.append(snap.policy_system)
                    if snap.remedy_system:
                        injects.append(snap.remedy_system)
                    with suppress(Exception):
                        from remedy.core.project_learning import pinned_constraints_block

                        pin = pinned_constraints_block(project_path)
                        if pin:
                            injects.append(pin)
                    if injects:
                        messages.insert(
                            -1,
                            {
                                "role": "system",
                                "content": "\n\n".join(injects),
                            },
                        )

                    if level == "strong":
                        tokens_before = est
                        messages[:] = prune_messages_for_send(
                            messages,
                            max_tool_chars=max(
                                4_000, (_TOOL_RESULT_CHAR_CAP or 64_000) // 4
                            ),
                            dedupe_tools=True,
                            collapse_completed_tools=True,
                            keep_recent_tool_pairs=4,
                        )
                        with suppress(Exception):
                            brief = getattr(self, "_session_brief", None)
                            if brief is not None:
                                from remedy.core.session_quality import get_session_quality
                                from remedy.memory.harness.quality import (
                                    review_compress_quality,
                                )

                                pre_hist = list(messages)
                                self._session_brief = heuristic_merge_from_history(
                                    brief, messages, intent_hint=message
                                )
                                tokens_after = estimate_tokens(
                                    messages,
                                    provider=provider or None,
                                    model=model or None,
                                )
                                q = review_compress_quality(
                                    messages_before=pre_hist,
                                    brief=self._session_brief,
                                    tokens_before=tokens_before,
                                    tokens_after=tokens_after,
                                )
                                get_session_quality(sid).record_compress(
                                    tokens_before=tokens_before,
                                    tokens_after=tokens_after,
                                    quality=q,
                                    source="auto_strong",
                                )
                        messages.insert(-1, compression_nudge_message("strong"))
                        with suppress(Exception):
                            from remedy.core.metrics import default_registry

                            default_registry.counter(
                                "remedy_context_auto_compress_total", level="strong"
                            ).inc()
                    elif level == "soft":
                        # Soft: structural collapse of old tools without hard cap first
                        with suppress(Exception):
                            messages[:] = prune_messages_for_send(
                                messages,
                                dedupe_tools=True,
                                collapse_completed_tools=True,
                                keep_recent_tool_pairs=6,
                            )
                        messages.insert(-1, compression_nudge_message(level))
                    with suppress(Exception):
                        from remedy.core.metrics import default_registry

                        default_registry.gauge("remedy_context_tokens_estimate").set(
                            float(est)
                        )
            all_tools = self._openai_tools()
            if plan_mode:
                from remedy.core.plan_store import PLAN_MODE_TOOL_NAMES

                tools = [
                    t
                    for t in all_tools
                    if ((t.get("function") or {}).get("name") or "") in PLAN_MODE_TOOL_NAMES
                ]
            else:
                # Creative image prompts ("make something cool/spacey") must keep tools on.
                tools = (
                    all_tools
                    if should_enable_tools(
                        message, all_tools, has_attachments=bool(attachments)
                    )
                    or bool(
                        re.search(
                            r"\b(comfy|image|picture|nebula|spacey|generate|draw|illustrat)\b",
                            message or "",
                            re.I,
                        )
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

            # Long agent runs: high wall-clock + read idle so multi-step work
            # (and long thinking streams) are not killed mid-flight.
            timeout = aiohttp.ClientTimeout(total=3_600, sock_read=900, connect=60)
            connector = aiohttp.TCPConnector(
                limit=24,
                ttl_dns_cache=300,
            )
            # Auto-continue after finish_reason=length / max_tokens until complete.
            # No artificial short-answer wall — keep going until the model finishes.
            max_length_continuations = 10_000
            length_continuations = 0
            # Retry once after repairing DeepSeek reasoning_content on tool turns.
            reasoning_repair_done = False
            # Soft API errors: keep going when we already have tool context.
            api_soft_failures = 0
            max_api_soft_failures = 16
            # Sticky force-answer after recoverable provider failures.
            force_answer_sticky = False
            # Empty-answer recovery (model thought but sent no content).
            empty_answer_retries = 0
            max_empty_answer_retries = 8
            # One OAuth/API re-auth attempt per turn (xAI 401 → refresh token).
            auth_refresh_done = False
            async with aiohttp.ClientSession(
                timeout=timeout, connector=connector
            ) as http:
                for step in range(self._max_react_steps):
                    is_final_step = step >= self._max_react_steps - 1
                    # Only force a final answer at the true step wall (or sticky).
                    # Early force-answer (old: step>=8) made long tool chains "stuck".
                    force_answer = (
                        is_final_step or not tools or force_answer_sticky
                    )
                    step_tools = None if force_answer else tools

                    if force_answer and step > 0 and length_continuations == 0:
                        # Never ask for a "short" answer — complete full response.
                        messages.append(
                            {
                                "role": "user",
                                "content": (
                                    "Stop calling tools. Using the information above, "
                                    "give your complete final answer to the user now. "
                                    "Be thorough and finish every section — "
                                    "do not cut off mid-section or summarize-only."
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
                        thinking_level=getattr(self, "_thinking_level", "high"),
                    )

                    collected: dict[str, Any] = {"content": None, "tool_calls": None}
                    round_state = StreamRoundState()

                    _llm_t0 = time.perf_counter()
                    async with http.post(
                        endpoint, headers=headers, json=body
                    ) as resp:
                        _llm_ms = (time.perf_counter() - _llm_t0) * 1000.0
                        if resp.status != 200:
                            text = await resp.text()
                            logger.error(
                                "LLM API error %d: %s", resp.status, text[:500]
                            )
                            with suppress(Exception):
                                from remedy.nanoswarm import get_swarm
                                from remedy.nanoswarm.events import SwarmEvent

                                get_swarm().dispatch(
                                    SwarmEvent.provider_health(
                                        provider=getattr(self, "_llm_provider", None),
                                        model=getattr(self, "_llm_model", None),
                                        ok=False,
                                        latency_ms=_llm_ms,
                                        error=text[:200],
                                        status_code=int(resp.status),
                                    )
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

                        with suppress(Exception):
                            from remedy.nanoswarm import get_swarm
                            from remedy.nanoswarm.events import SwarmEvent

                            get_swarm().dispatch(
                                SwarmEvent.provider_health(
                                    provider=getattr(self, "_llm_provider", None),
                                    model=getattr(self, "_llm_model", None),
                                    ok=True,
                                    latency_ms=_llm_ms,
                                    status_code=200,
                                )
                            )

                        # Live-stream final-answer rounds (no tools this step).
                        # Buffer when tools are enabled — DeepSeek-class models
                        # often dump DSML tool markup as content if we stream live.
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
                            # DeepSeek thinking can pause for minutes between tokens.
                            # 120s was killing long reasoner streams mid-thought.
                            sse_idle_timeout = 900.0
                            while True:
                                try:
                                    line = await asyncio.wait_for(
                                        content_iter.__anext__(),
                                        timeout=sse_idle_timeout,
                                    )
                                except StopAsyncIteration:
                                    break
                                except TimeoutError:
                                    logger.warning(
                                        "SSE stream idle >%.0fs; ending this model round "
                                        "(will continue/promote reasoning if any)",
                                        sse_idle_timeout,
                                    )
                                    break
                                line_text = line.decode("utf-8").strip()
                                if line_text == "data: [DONE]":
                                    break
                                chunk = parse_sse_data_line(line_text)
                                if chunk is None:
                                    continue
                                # Provider usage (often only on final SSE chunk)
                                try:
                                    from remedy.core.usage import usage_from_provider_payload

                                    u = usage_from_provider_payload(
                                        chunk,
                                        model=getattr(self, "_llm_model", None),
                                        provider=getattr(self, "_llm_provider", None),
                                    )
                                    if u:
                                        try:
                                            from remedy.core.usage import observe_provider_usage
                                            from remedy.core.usage_ledger import (
                                                record_usage_event,
                                            )
                                            from remedy.nanoswarm.token_nanobot import (
                                                get_token_nanobot,
                                            )

                                            pt = int(u.get("prompt_tokens") or 0)
                                            ct = int(u.get("completion_tokens") or 0)
                                            est = int(get_token_nanobot().last_estimate or 0)
                                            prov = getattr(self, "_llm_provider", None)
                                            mod = getattr(self, "_llm_model", None)
                                            if pt > 0 and est > 0:
                                                observe_provider_usage(
                                                    est,
                                                    pt,
                                                    provider=prov,
                                                    model=mod,
                                                )
                                            if pt or ct:
                                                with suppress(Exception):
                                                    from remedy.core.session_quality import (
                                                        get_session_quality,
                                                    )

                                                    get_session_quality(
                                                        str(
                                                            getattr(self, "_session_id", "")
                                                            or ""
                                                        )
                                                    ).record_turn(
                                                        prompt_tokens=pt,
                                                        completion_tokens=ct,
                                                    )
                                                with suppress(Exception):
                                                    record_usage_event(
                                                        session_id=str(
                                                            getattr(self, "_session_id", "")
                                                            or ""
                                                        )
                                                        or None,
                                                        provider=prov,
                                                        model=mod,
                                                        prompt_tokens=pt,
                                                        completion_tokens=ct,
                                                        total_tokens=int(
                                                            u.get("total_tokens") or (pt + ct)
                                                        ),
                                                        estimated_cost_usd=float(
                                                            u.get("estimated_cost_usd") or 0
                                                        ),
                                                        source=str(u.get("source") or "provider"),
                                                    )
                                        except Exception:
                                            pass
                                        yield (
                                            "@@usage:"
                                            + json.dumps(u, separators=(",", ":"))
                                        )
                                except Exception:
                                    pass
                                r_before = len(''.join(round_state.reasoning_parts))
                                live = apply_openai_sse_chunk(
                                    round_state, chunk, stream_live=stream_live
                                )
                                r_after = ''.join(round_state.reasoning_parts)
                                if len(r_after) > r_before:
                                    yield f'@@thinking:{r_after[r_before:]}'
                                if live:
                                    produced_user_text = True
                                    yield live
                        else:
                            data = await resp.json()
                            try:
                                from remedy.core.usage import usage_from_provider_payload

                                u = usage_from_provider_payload(
                                    data,
                                    model=getattr(self, "_llm_model", None),
                                    provider=getattr(self, "_llm_provider", None),
                                )
                                if u:
                                    yield (
                                        "@@usage:"
                                        + json.dumps(u, separators=(",", ":"))
                                    )
                            except Exception:
                                pass
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
                    # Never treat DSML / text-tool dumps as user-visible answer text.
                    if text_out and _looks_like_pseudo_tools(text_out):
                        recovered_preview = _parse_pseudo_tool_calls(text_out)
                        clean = strip_tool_markup(text_out)
                        # Keep only non-markup prose (if any) for the bubble.
                        text_out = clean if clean and not _looks_like_pseudo_tools(clean) else ""
                        if not tool_calls_list and recovered_preview:
                            # Force recovery path below even if tools were off this round.
                            pass
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

                    # Recovery: model wrote tool calls as plain text / DSML → run them for real.
                    raw_round = finalize_round_text(round_state, tool_calls_list)
                    if (
                        not tool_calls_list
                        and raw_round
                        and _looks_like_pseudo_tools(raw_round)
                        and all_tools
                        and not pseudo_recovery_done
                        and not force_answer
                    ):
                        recovered = _parse_pseudo_tool_calls(raw_round)
                        if recovered:
                            pseudo_recovery_done = True
                            tools = all_tools  # ensure schemas stay available
                            recovered = normalize_tool_calls(recovered)
                            yield "@@tool_calls"
                            messages.append(
                                build_assistant_api_message(
                                    content=(
                                        "Using tools now (recovered from a non-native tool dump)."
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
                                with suppress(Exception):
                                    md = self._maybe_auto_checkpoint(
                                        reason="recovery",
                                        title="After tool failure",
                                        force=True,
                                    )
                                    if md:
                                        yield "@@checkpoint"
                            with suppress(Exception):
                                self._maybe_auto_checkpoint(reason="auto")
                            continue

                    if text_out and (not tool_calls_list or force_answer):
                        # Don't ship faux tool syntax as the final answer.
                        if (
                            raw_round
                            and _looks_like_pseudo_tools(raw_round)
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
                                        "Do not write tool calls as text or DSML/XML. "
                                        "Use the function-calling API now "
                                        "(comfyui / local_discover / file_read / "
                                        "list_dir / bash_exec), or answer from context."
                                    ),
                                }
                            )
                            continue
                        if stream_live and produced_user_text:
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
                            # Final safety: never yield markup-only blobs.
                            if text_out and not _looks_like_pseudo_tools(text_out):
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
                        # Empty content after tools/thinking: never soft-give-up while
                        # we still have budget. DeepSeek often leaves content blank
                        # after a long reasoning stream — promote reasoning first.
                        if not produced_user_text:
                            # 1) Reasoning-only answer (common for reasoner models).
                            if reasoning_out and not _looks_like_pseudo_tools(
                                reasoning_out
                            ):
                                yield reasoning_out
                                produced_user_text = True
                                if (
                                    round_state.hit_length_limit
                                    and length_continuations
                                    < max_length_continuations
                                ):
                                    length_continuations += 1
                                    messages.append(
                                        {
                                            "role": "assistant",
                                            "content": reasoning_out,
                                            "reasoning_content": reasoning_out,
                                        }
                                    )
                                    messages.append(
                                        {
                                            "role": "user",
                                            "content": (
                                                "Continue your final answer exactly where "
                                                "you stopped. Do not restart or summarize."
                                            ),
                                        }
                                    )
                                    tools = []
                                    force_answer_sticky = True
                                    continue
                                return
                            # 2) Retry synthesis — do not abandon the user mid-task.
                            if (
                                empty_answer_retries < max_empty_answer_retries
                                and not is_final_step
                            ):
                                empty_answer_retries += 1
                                force_answer_sticky = True
                                tools = []
                                messages.append(
                                    {
                                        "role": "user",
                                        "content": (
                                            "You gathered context (tools and/or thinking) "
                                            "but returned no final answer text. "
                                            "Write the complete final answer now as plain "
                                            "chat content — full review, not a stub. "
                                            "Do not call tools."
                                        ),
                                    }
                                )
                                logger.info(
                                    "Empty answer retry %d/%d after step %d",
                                    empty_answer_retries,
                                    max_empty_answer_retries,
                                    step + 1,
                                )
                                continue
                            # 3) Last resort only after retries exhausted.
                            yield (
                                "I gathered context but the model returned an empty "
                                "final message after several retries. Please resend "
                                "or ask me to continue from where I left off."
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
                        with suppress(Exception):
                            self._maybe_auto_checkpoint(
                                reason="recovery",
                                title="After tool failure",
                                force=True,
                            )
                    with suppress(Exception):
                        self._maybe_auto_checkpoint(reason="auto")
                    if is_final_step:
                        with suppress(Exception):
                            md = self._maybe_auto_checkpoint(
                                reason="step_wall",
                                title="Approaching step limit",
                                force=True,
                            )
                            if md:
                                yield "@@checkpoint"

            # Exhausted steps without a streamed answer — full synthesis, not a stub.
            if not produced_user_text:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Using all tool results and context above, write the "
                            "complete final answer now. Be thorough — do not give a "
                            "one-line stub or say you cannot answer if context exists."
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
                    thinking_level=getattr(self, "_thinking_level", "high"),
                )
                try:
                    async with aiohttp.ClientSession(
                        timeout=aiohttp.ClientTimeout(total=900, sock_read=900)
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
                                    # Also surface trailing reasoning as answer if
                                    # content stayed empty (DeepSeek reasoner).
                                    reason = (
                                        delta.get("reasoning_content")
                                        or delta.get("reasoning")
                                    )
                                    if (
                                        not piece
                                        and isinstance(reason, str)
                                        and reason
                                    ):
                                        produced_user_text = True
                                        yield reason
                            else:
                                data = await resp.json()
                                parsed = self._provider.extract_response(data)
                                piece = parsed.get("content") or parsed.get(
                                    "reasoning_content"
                                )
                                if piece:
                                    produced_user_text = True
                                    yield str(piece)
                except Exception:
                    logger.debug("final synthesis failed", exc_info=True)
            if not produced_user_text:
                yield (
                    "I finished the tool loop but still have no final model text. "
                    "Ask me to **continue** or restate the request and I will resume "
                    "from the context already gathered."
                )
            # Compound learning + speculative warm for next turn
            with suppress(Exception):
                from remedy.core.project_learning import record_session_end
                from remedy.core.session_quality import get_session_quality
                from remedy.core.speculative import schedule_speculative_prep

                sid = str(getattr(self, "_session_id", "") or "")
                qsnap = get_session_quality(sid).snapshot()
                project_path = str(
                    getattr(self.config, "project_path", None)
                    or getattr(self, "_project_path", None)
                    or ""
                ) or None
                # Light touch each turn (not only true session end)
                if project_path and int(qsnap.get("turns") or 0) > 0:
                    # Only merge full profile every few turns to limit disk IO
                    if int(qsnap.get("turns") or 0) % 5 == 0:
                        record_session_end(project_path, qsnap)
                schedule_speculative_prep(
                    session_id=sid,
                    brief=getattr(self, "_session_brief", None),
                    messages=getattr(self, "_last_send_messages", None),
                    user_text=message or "",
                    project_path=project_path,
                    memory=getattr(self, "memory", None),
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
        """Bind tools/cwd to the **session** project for this turn.

        Tree contract: a session under ``📁 RemedyAI`` uses that folder as the
        tool jail; a **No project** session gets unset → full access. Global
        Settings default is only used when there is no session row.
        """
        if session_id:
            self._session_id = session_id
        session_path: str | None = None
        has_session_row = False
        if session_id and self.memory is not None:
            with suppress(Exception):
                sess = await self.memory.get_chat_session(session_id)
                if sess is not None:
                    has_session_row = True
                    session_path = getattr(sess, "project_path", None)
        if has_session_row:
            # Session owns workspace for this turn (do not keep prior session raw).
            if session_path and not is_unset_project_path(session_path):
                self.set_project_path(session_path, as_default=False)
            else:
                # Explicit no-project → full access for this turn
                self.set_project_path(None, as_default=False)
            return
        # No session row: fall back to configured default project
        cfg_path = None
        if getattr(self, "config", None) is not None:
            cfg_path = getattr(self.config, "project_path", None)
        if is_unset_project_path(cfg_path):
            self.set_project_path(None, as_default=False)
        else:
            self.set_project_path(str(cfg_path), as_default=False)

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
        parts = []
        # Project workspace (OpenCode-style default directory for this session)
        with suppress(Exception):
            parts.append(
                workspace_context_block(
                    self.effective_project_path(),
                    access_scope=self.access_scope(),
                    extra_roots=self.allowed_roots(),
                    project_unset=self.project_path_is_unset(),
                )
            )

        # Partner Memory (durable identity + preferences — default on, budget-capped)
        with suppress(Exception):
            if self.memory is not None:
                from remedy.memory.partner_memory import build_partner_memory_block

                profile = await self.memory.get_or_create_profile()
                # Config user_name is the settings field; prefer live profile, fall back to config.
                if not (profile.display_name or "").strip():
                    try:
                        from remedy.interfaces.config import load_config

                        user_name = str(load_config().get("user_name") or "").strip()
                        if user_name:
                            profile.display_name = user_name
                            await self.memory.save_user_profile(profile)
                    except Exception:
                        pass
                # Prefer query-aware ranking when last user message is known
                q = str(getattr(self, "_last_user_text", "") or "")
                project_path = str(
                    getattr(self.config, "project_path", None)
                    or getattr(self, "_project_path", None)
                    or ""
                ) or None
                # Light reinforce of matching facts (same session continuity)
                with suppress(Exception):
                    from remedy.memory.partner_memory import reinforce_matching

                    if q and reinforce_matching(profile, q):
                        await self.memory.save_user_profile(profile)
                block = build_partner_memory_block(
                    profile, query=q, project_path=project_path
                )
                if block:
                    parts.append(block)
                # Full-scope reminder (no project jail) — once per context build
                if self.project_path_is_unset() or self.access_scope() == "full":
                    parts.append(
                        "Access scope: full (no project folder). "
                        "Tools are not limited to a project jail — prefer "
                        "asking the user to pick a folder for focused coding, "
                        "and avoid broad writes outside the active task."
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

        # Skills catalog (progressive disclosure stage 1) — ranked, not full bodies.
        with suppress(Exception):
            reg = getattr(self, "skills", None)
            count = int(getattr(reg, "count", 0) or 0) if reg is not None else 0
            if reg is not None and count > 0:
                ws = str(self.effective_project_path())
                # Single ranked catalog with workspace hint (no double rank / discard)
                ranked_lines = reg.summary_lines(limit=24, query="")
                if hasattr(reg, "match_skills"):
                    top = reg.match_skills(
                        "",
                        limit=24,
                        workspace_hint=ws,
                    )
                    if top:
                        # Rebuild lines from ranked order with status badges
                        lines: list[str] = []
                        for skill, _sc in top:
                            m = skill.manifest
                            st = m.status.value if hasattr(m.status, "value") else str(m.status)
                            desc = (m.description or "").strip()
                            if len(desc) > 140:
                                desc = desc[:137] + "…"
                            lines.append(f"- **{m.name}** [{st}]: {desc}")
                        lines.append(
                            "_Activate with skill_activate(name=…); rank with skill_search._"
                        )
                        ranked_lines = lines
                parts.append(
                    "Skills catalog (name+status only — call skill_activate to load "
                    "full procedure; skill_search to rank by task):\n"
                    + "\n".join(ranked_lines)
                )
                with suppress(Exception):
                    from remedy.core.metrics import default_registry

                    default_registry.gauge(
                        "remedy_context_skills_listed"
                    ).set(float(min(count, 24)))
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
