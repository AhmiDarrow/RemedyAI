"""Local service tools: local_discover, comfyui, vision_decode.

Extracted from BasicRuntime so agent.py stays orchestrator-only.
"""

from __future__ import annotations

import asyncio
import json
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.core.errors import format_tool_error


def register_local_discover_tools(runtime: Any) -> None:
    """Portable discovery for *any* skill/service local deps — no disk thrash."""

    async def local_discover(
        action: str = "scan",
        target: str = "",
    ) -> str:
        """Find local services/binaries skills need on this machine.

        action=scan   → all skill local: specs + built-ins (comfyui, ollama, …)
        action=status → same as scan (alias)
        action=one    → single target id (e.g. comfyui, ollama)
        action=home   → last first-home stretch census
        action=stretch → re-probe hardware/tools/rooms (same as /stretch)
        """
        from remedy.core.local_discover import (
            collect_skill_local_specs,
            discover_all,
            discover_one,
        )

        act = (action or "scan").strip().lower()
        if act in ("home", "census", "stretch", "map"):
            from remedy.execution.host.stretch import (
                load_census,
                stretch_home,
            )

            home = getattr(getattr(runtime, "config", None), "home_dir", None)
            try:
                census = (
                    stretch_home(home, force=True)
                    if act in ("stretch", "map")
                    else load_census(home)
                )
            except Exception as e:
                return format_tool_error(
                    str(e),
                    code="STRETCH_FAILED",
                    tool_name="local_discover",
                    suggestion="Retry /stretch or action=stretch.",
                )
            if census is None:
                return format_tool_error(
                    "This home has not been stretched yet.",
                    code="NO_CENSUS",
                    tool_name="local_discover",
                    suggestion="Call local_discover action=stretch (or /stretch).",
                )
            return json.dumps(census.to_dict(), indent=2)
        specs = []
        with suppress(Exception):
            reg = getattr(runtime, "skills", None)
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

    runtime.tool_registry.register_builtin_handler(
        "local_discover",
        "Portable discovery of local services/binaries that skills need "
        "(ComfyUI, Ollama, anything declared in skill frontmatter local:). "
        "ALWAYS prefer this over list_dir/bash disk hunts. "
        "action=scan (default), action=one target=comfyui|ollama|…, "
        "action=home (last census), action=stretch (re-map this PC).",
        local_discover,
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": "scan | one | home | stretch (default scan)",
                },
                "target": {
                    "type": "string",
                    "description": "Service id when action=one (comfyui, ollama, …)",
                },
            },
        },
    )

def register_comfyui_tools(runtime: Any) -> None:
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
                sid = getattr(runtime, "_session_id", None)
                home = None
                if getattr(runtime, "config", None) is not None:
                    home = getattr(runtime.config, "home_dir", None)
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
                        md = comfy.markdown_for_image(meta, caption=text[:80])
                        blocks.append(md)
                        image_blocks.append(md)
                        runtime._track_artifact(str(meta.get("path") or img_path))
                    else:
                        # No session — reference the comfy_out file directly.
                        meta = {
                            "name": img_path.name,
                            "path": str(img_path),
                            "mime": "image/png",
                            "view_url": "",
                            "home_dir": str(home) if home else None,
                        }
                        md = comfy.markdown_for_image(meta, caption=text[:80])
                        blocks.append(md)
                        image_blocks.append(md)
                        runtime._track_artifact(str(img_path))
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

    runtime.tool_registry.register_builtin_handler(
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

def register_vision_tools(runtime: Any) -> None:
    """Local visual decoder: status / install / decode image paths."""

    async def vision_decode(
        action: str = "status",
        path: str = "",
        question: str = "",
        prefer_cuda: bool = False,
    ) -> str:
        """Local visual decoder (llama.cpp + SmolVLM2 2.2B).

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
        if getattr(runtime, "config", None) is not None:
            home = getattr(runtime.config, "home_dir", None)
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
                    img = runtime.resolve_tool_path(p)
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

    runtime.tool_registry.register_builtin_handler(
        "vision_decode",
        "Local visual decoder (SmolVLM2 2.2B via llama.cpp). "
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


def _rmb_home(runtime: Any) -> Any:
    cfg = getattr(runtime, "config", None)
    return getattr(cfg, "home_dir", None) if cfg is not None else None


def _rmb_cfg(runtime: Any) -> dict[str, Any]:
    out: dict[str, Any] = {}
    with suppress(Exception):
        from remedy.interfaces.config import load_config

        raw = load_config() or {}
        if isinstance(raw, dict):
            out = dict(raw)
    home = _rmb_home(runtime)
    if home:
        out["home_dir"] = home
    return out


def _rmb_approval(runtime: Any, cmd: str, reason: str) -> str:
    from remedy.core.approvals import APPROVALS
    from remedy.core.turn_context import turn_session_id

    sid = turn_session_id(runtime)
    # Honor Auto/Full: do not invent a checkpoint Settings would not show.
    ask_reason = APPROVALS.needs_ask(cmd, tool_name="rmb")
    if not ask_reason:
        return ""
    if APPROVALS.is_approved("rmb", cmd, session_id=sid):
        return ""
    item = APPROVALS.create(
        tool_name="rmb",
        command=cmd,
        reason=ask_reason or reason,
        session_id=sid,
    )
    return (
        f"APPROVAL_REQUIRED id={item.id}\n"
        f"reason={ask_reason or reason}\n"
        "Do not invent success. Tell the owner this needs approval "
        f"in the UI (or /approve {item.id}), then retry."
    )


def _this_turn_is_rmb(runtime: Any) -> bool:
    with suppress(Exception):
        from remedy.core.llm_binding import get_llm_binding
        from remedy.runtime.rmb.mode import is_rmb_provider

        bind = get_llm_binding(runtime)
        return is_rmb_provider(
            getattr(bind, "provider", None), getattr(bind, "base_url", None)
        )
    return False


def _note_rmb_map(runtime: Any, data: dict[str, Any]) -> None:
    with suppress(Exception):
        from remedy.core.metabolism.machine_map import get_machine_map
        from remedy.core.turn_context import turn_session_id

        get_machine_map(turn_session_id(runtime)).note_organ("rmb", data, ttl_s=20.0)


def register_rmb_tools(runtime: Any) -> None:
    """RMB — her own local llama.cpp muscle. Not a second product."""

    async def rmb(
        action: str = "status",
        query: str = "",
        repo: str = "",
        filename: str = "",
        profile: str = "",
        model_id: str = "",
        model_path: str = "",
        ctx_size: int | None = None,
        n_gpu_layers: int | None = None,
        auto_start: bool | None = None,
        thinking: str = "",
        enable_mtp: bool | None = None,
        n_cpu_moe: int | None = None,
        settings: dict | str | None = None,
        load: bool = True,
    ) -> str:
        """Drive Remedy Muscle Bridge (local llama.cpp on :8787).

        action=status  → running/ready/model/local GGUFs/MTP (no start)
        action=start   → enable + start host (autofit default)
        action=stop    → stop host; vision can use the GPU again
        action=use     → start and switch *this* chat to RMB
        action=catalog → bundled Hugging Face GGUF catalog
        action=models  → GGUFs already in ~/.remedy/rmb/models/ (and search roots)
        action=search  → Hugging Face name / owner/repo / file URL
        action=files   → no repo: local GGUFs; with repo: list .gguf on HF
        action=pull    → download into ~/.remedy/rmb/models/
        action=settings → no patch: dump live config; else patch (may restart)

        Autofit is default. Do not guess Hugging Face hosts — search, then pick.
        Starting RMB suspends SmolVLM. Never glob C:\\Users for GGUFs —
        action=status / action=models lists the house first.
        """
        act = (action or "status").strip().lower()
        home = _rmb_home(runtime)
        cfg = _rmb_cfg(runtime)

        if act in ("status", "info", "health"):
            from remedy.runtime.rmb.service import get_rmb_status

            st = await asyncio.to_thread(get_rmb_status, cfg)
            _note_rmb_map(
                runtime,
                {
                    "running": bool(st.get("running")),
                    "ready": bool(st.get("ready")),
                    "model": str(st.get("chat_model") or st.get("model_id") or ""),
                    "profile": str(st.get("profile") or "autofit"),
                    "ctx": int(st.get("ctx_size") or 0),
                    "vision_suspended": bool(st.get("vision_suspended")),
                },
            )
            public: dict[str, Any] = {
                k: st.get(k)
                for k in (
                    "ok",
                    "brand",
                    "brand_full",
                    "enabled",
                    "auto_start",
                    "installed",
                    "running",
                    "ready",
                    "starting",
                    "loading",
                    "user_stopped",
                    "base_url",
                    "port",
                    "model_id",
                    "chat_model",
                    "model_path",
                    "model_present",
                    "runtime_present",
                    "ctx_size",
                    "n_gpu_layers",
                    "profile",
                    "vision_suspended",
                    "last_error",
                    "not_ready_hint",
                )
                if k in st
            }
            ggufs = st.get("discovered_ggufs")
            if isinstance(ggufs, list):
                public["local_ggufs"] = [
                    {
                        "name": g.get("name"),
                        "size_gb": g.get("size_gb"),
                        "dir": g.get("dir"),
                        "path": g.get("path"),
                    }
                    for g in ggufs[:24]
                    if isinstance(g, dict)
                ]
            ha = st.get("host_auto")
            if isinstance(ha, dict):
                public["host_auto"] = {
                    k: ha.get(k)
                    for k in (
                        "mtp",
                        "mtp_armed",
                        "draft_armed",
                        "parallel_effective",
                        "model_draft",
                        "spec_type",
                        "spec_draft_n_max",
                        "thinking_mode",
                        "summary",
                        "unfit",
                        "warnings",
                    )
                    if k in ha
                }
            af = st.get("autofit")
            if isinstance(af, dict):
                public["autofit"] = {
                    k: af.get(k)
                    for k in ("summary", "ctx_size", "n_gpu_layers", "target")
                    if k in af
                }
            public["next"] = (
                "House GGUFs are local_ggufs. Point at one with "
                'rmb action="settings" model_path=… then '
                "rmb action=start (then action=use to chat on it). "
                "Do not glob C:\\Users. Do not edit the git repo to load a model."
                if not st.get("ready")
                else "ready — rmb action=use switches this chat onto the local host."
            )
            return json.dumps(public, indent=2, default=str)

        if act in ("catalog",):
            from remedy.runtime.rmb.catalog import catalog_public

            return json.dumps(catalog_public(), indent=2, default=str)

        if act in ("models", "inventory", "local"):
            from remedy.runtime.rmb.service import discover_ggufs

            found = await asyncio.to_thread(discover_ggufs, home)
            return json.dumps(
                {
                    "ok": True,
                    "house": str(home) if home else "~/.remedy/rmb/models",
                    "ggufs": found[:48],
                    "next": (
                        'rmb action="settings" model_path="<path>" to load one. '
                        "Sibling mtp-<stem>.gguf next to the main file auto-arms MTP."
                    ),
                },
                indent=2,
                default=str,
            )

        if act in ("search", "hf"):
            q = (query or repo or "").strip()
            if not q:
                return format_tool_error(
                    "query is required for action=search",
                    code="MISSING_QUERY",
                    tool_name="rmb",
                    suggestion=(
                        'rmb action="search" query="Qwen3.5-9B" '
                        "or query=\"owner/repo\" or a Hugging Face file URL."
                    ),
                )
            from remedy.runtime.rmb.hf import HfError, resolve_query

            try:
                out = await asyncio.to_thread(resolve_query, q)
            except HfError as e:
                return format_tool_error(
                    str(e),
                    code="HF_SEARCH",
                    tool_name="rmb",
                    suggestion="Paste owner/repo or a /resolve/…gguf URL. Do not guess the host.",
                )
            return json.dumps(out, indent=2, default=str)

        if act in ("files", "list"):
            r = (repo or query or "").strip()
            if not r:
                from remedy.runtime.rmb.service import discover_ggufs

                found = await asyncio.to_thread(discover_ggufs, home)
                return json.dumps(
                    {
                        "ok": True,
                        "house": True,
                        "ggufs": found[:48],
                        "next": (
                            "These are local GGUFs. For Hugging Face listing pass "
                            'repo="owner/repo". Load one with '
                            'rmb action="settings" model_path=…'
                        ),
                    },
                    indent=2,
                    default=str,
                )
            from remedy.runtime.rmb.hf import HfError, list_gguf_files, sanitize_repo

            try:
                clean = sanitize_repo(r)
                files = await asyncio.to_thread(list_gguf_files, clean)
            except HfError as e:
                return format_tool_error(str(e), code="HF_FILES", tool_name="rmb")
            return json.dumps({"ok": True, "repo": clean, "files": files}, indent=2)

        restart_acts = ("start", "stop", "use", "settings")
        if (
            act in restart_acts or (act in ("pull", "download") and load)
        ) and _this_turn_is_rmb(runtime):
            return format_tool_error(
                "This turn is already on RMB — restarting the host would cut the reply. "
                "Finish this message, then start/stop from Settings or a new chat.",
                code="RMB_SELF",
                tool_name="rmb",
                suggestion="Use a cloud chat (or a new session) to restart RMB.",
            )

        if act in ("start", "run", "up"):
            gate = _rmb_approval(
                runtime,
                "rmb start",
                "Start the local RMB llama.cpp host (uses this PC's GPU/RAM; "
                "suspends SmolVLM while it runs).",
            )
            if gate:
                return gate
            from remedy.runtime.rmb.service import apply_rmb_settings, start_rmb_server

            await asyncio.to_thread(
                apply_rmb_settings, {"enabled": True}, home_dir=home, cfg=cfg
            )
            result = await asyncio.to_thread(
                start_rmb_server, home_dir=home, wait_s=120.0, clear_user_stopped=True
            )
            _note_rmb_map(
                runtime,
                {
                    "running": bool(result.get("ok") or result.get("running")),
                    "ready": bool(result.get("ok") or result.get("ready")),
                    "profile": "autofit",
                },
            )
            return json.dumps(result, indent=2, default=str)

        if act in ("stop", "down"):
            gate = _rmb_approval(
                runtime,
                "rmb stop",
                "Stop the local RMB host so vision can use the GPU again.",
            )
            if gate:
                return gate
            from remedy.runtime.rmb.service import stop_rmb_server

            result = await asyncio.to_thread(stop_rmb_server, home_dir=home)
            _note_rmb_map(runtime, {"running": False, "ready": False})
            return json.dumps(result, indent=2, default=str)

        if act in ("use", "switch", "chat"):
            gate = _rmb_approval(
                runtime,
                "rmb use",
                "Start RMB and switch this chat onto the local model.",
            )
            if gate:
                return gate
            from pathlib import Path as _P

            from remedy.interfaces.api_support import _apply_llm_to_runtime
            from remedy.runtime.rmb.catalog import DEFAULT_RMB_MODEL_ID, RMB_MODELS, get_model_spec
            from remedy.runtime.rmb.config import load_rmb_json, merge_state
            from remedy.runtime.rmb.service import apply_rmb_settings, start_rmb_server

            st = await asyncio.to_thread(
                apply_rmb_settings,
                {"enabled": True, "use_as_chat_provider": True},
                home_dir=home,
                cfg=cfg,
            )
            start = await asyncio.to_thread(
                start_rmb_server,
                home_dir=home,
                wait_s=120.0,
                clear_user_stopped=True,
            )
            rstate = merge_state(load_rmb_json(home))
            base = str(rstate.get("base_url") or "http://127.0.0.1:8787/v1")
            model = ""
            if rstate.get("model_path"):
                model = _P(str(rstate["model_path"])).stem
            if not model:
                mid = str(rstate.get("model_id") or DEFAULT_RMB_MODEL_ID)
                if mid in RMB_MODELS:
                    model = get_model_spec(mid).filename.replace(".gguf", "")
                else:
                    model = mid
            applied = False
            if model:
                with suppress(Exception):
                    _apply_llm_to_runtime(
                        runtime,
                        provider="rmb",
                        model=model,
                        base_url=base,
                        api_key="rmb",
                        harness_mode="auto",
                        harness_min_context_pct=0.55,
                        harness_max_context_pct=0.78,
                    )
                    applied = True
            _note_rmb_map(
                runtime,
                {
                    "running": True,
                    "ready": bool(start.get("ok") or start.get("ready")),
                    "model": model,
                    "profile": str(rstate.get("profile") or "autofit"),
                },
            )
            return json.dumps(
                {
                    "status": st,
                    "start": start,
                    "runtime_applied": applied,
                    "chat_model": model,
                    "next": (
                        "This chat is now RMB. Send a new message to talk on the local host. "
                        "SmolVLM stays suspended until rmb action=stop."
                    ),
                },
                indent=2,
                default=str,
            )

        if act in ("settings", "configure", "set"):
            patch: dict[str, Any] = {}
            if isinstance(settings, str) and settings.strip():
                try:
                    parsed = json.loads(settings)
                except json.JSONDecodeError as e:
                    return format_tool_error(
                        f"settings is not valid JSON: {e}",
                        code="BAD_JSON",
                        tool_name="rmb",
                    )
                if isinstance(parsed, dict):
                    patch.update(parsed)
            elif isinstance(settings, dict):
                patch.update(settings)
            if profile:
                patch["profile"] = profile
            if model_id:
                patch["model_id"] = model_id
            if model_path:
                patch["model_path"] = model_path
            if ctx_size is not None:
                patch["ctx_size"] = int(ctx_size)
            if n_gpu_layers is not None:
                patch["n_gpu_layers"] = int(n_gpu_layers)
            if auto_start is not None:
                patch["auto_start"] = bool(auto_start)
            if thinking:
                from remedy.runtime.rmb.host_profile import thinking_value_known

                if not thinking_value_known(thinking):
                    return format_tool_error(
                        f"thinking must be on/off (got {thinking!r}) — an "
                        "unrecognized word would silently leave thinking ON.",
                        code="BAD_THINKING",
                        tool_name="rmb",
                        suggestion='rmb action="settings" thinking="off"',
                    )
                patch["thinking"] = thinking
            if enable_mtp is not None:
                patch["enable_mtp"] = bool(enable_mtp)
            if n_cpu_moe is not None:
                patch["n_cpu_moe"] = int(n_cpu_moe)
            if not patch:
                from remedy.runtime.rmb.config import load_rmb_json, merge_state

                live = merge_state(load_rmb_json(home))
                public_live: dict[str, Any] = {
                    k: live.get(k)
                    for k in (
                        "enabled",
                        "model_id",
                        "model_path",
                        "n_gpu_layers",
                        "ctx_size",
                        "profile",
                        "autofit",
                        "autofit_locked",
                        "host_auto",
                        "cache_type",
                        "flash_attn",
                        "temperature",
                        "thinking",
                        "reasoning_budget",
                        "enable_mtp",
                        "n_cpu_moe",
                        "spec_draft_n_max",
                        "n_gpu_layers_draft",
                        "model_draft",
                        "use_jinja",
                        "no_mmap",
                        "cache_reuse",
                    )
                    if k in live
                }
                public_live["ok"] = True
                public_live["next"] = (
                    'Patch with rmb action="settings" thinking="off" '
                    'or enable_mtp=false or n_cpu_moe=99 (-1 = all experts on '
                    'GPU) or profile="turbo" '
                    "or n_gpu_layers=40 or ctx_size=4096 or model_path=…"
                )
                return json.dumps(public_live, indent=2, default=str)
            gate = _rmb_approval(
                runtime,
                f"rmb settings {sorted(patch.keys())}",
                "Change RMB host settings (may restart llama-server).",
            )
            if gate:
                return gate
            from remedy.runtime.rmb.service import apply_rmb_settings

            result = await asyncio.to_thread(
                apply_rmb_settings, patch, home_dir=home, cfg=cfg, live=True, wait_s=120.0
            )
            return json.dumps(result, indent=2, default=str)

        if act in ("pull", "download"):
            q = (query or "").strip()
            r = (repo or "").strip()
            fn = (filename or "").strip()
            if not q and not (r and fn):
                return format_tool_error(
                    "pull needs query= (file URL or owner/repo + filename) "
                    "or repo= and filename=.",
                    code="MISSING_PULL",
                    tool_name="rmb",
                    suggestion=(
                        'rmb action="search" first, then '
                        'rmb action="pull" repo="owner/repo" filename="….gguf"'
                    ),
                )
            gate = _rmb_approval(
                runtime,
                f"rmb pull {q or r}/{fn}",
                "Download a GGUF into ~/.remedy/rmb/models/ (can be large).",
            )
            if gate:
                return gate
            from remedy.runtime.rmb.hf import HfError, parse_hf_hint, start_pull

            url = ""
            rev = None
            if q and not (r and fn):
                try:
                    hint = parse_hf_hint(q)
                except HfError as e:
                    return format_tool_error(str(e), code="HF_HINT", tool_name="rmb")
                r = r or str(hint.repo or "")
                fn = fn or str(hint.filename or "")
                rev = hint.revision
                url = str(hint.url or "")
            try:
                out = await asyncio.to_thread(
                    start_pull,
                    repo=r or None,
                    filename=fn or None,
                    revision=rev,
                    url=url or None,
                    home_dir=home,
                    load=bool(load),
                )
            except HfError as e:
                return format_tool_error(str(e), code="HF_PULL", tool_name="rmb")
            return json.dumps(out, indent=2, default=str)

        if act in ("progress",):
            from remedy.runtime.rmb.hf import progress_snapshot

            return json.dumps(progress_snapshot(), indent=2, default=str)

        return format_tool_error(
            f"Unknown rmb action: {action!r}",
            code="UNKNOWN_ACTION",
            tool_name="rmb",
            suggestion=(
                "status | start | stop | use | catalog | models | search | files | "
                "pull | settings | progress"
            ),
        )

    runtime.tool_registry.register_builtin_handler(
        "rmb",
        "Remedy Muscle Bridge — her own local llama.cpp host (not a second product). "
        "status | start | stop | use (chat on it) | catalog | models (local GGUFs) | "
        "search | files | pull | settings | progress. Autofit default. Starting "
        "suspends SmolVLM. Local inventory is action=models / status.local_ggufs — "
        "never glob the user profile. Do not guess Hugging Face hosts — search first. "
        "Do not only point the owner at Settings.",
        rmb,
        {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "description": (
                        "status | start | stop | use | catalog | models | search | "
                        "files | pull | settings | progress (default status)"
                    ),
                },
                "query": {
                    "type": "string",
                    "description": "HF name, owner/repo, or file URL (search/pull)",
                },
                "repo": {"type": "string", "description": "owner/repo for files/pull"},
                "filename": {"type": "string", "description": ".gguf filename to pull"},
                "profile": {
                    "type": "string",
                    "description": "autofit | agent | turbo | quality (settings)",
                },
                "model_id": {"type": "string"},
                "model_path": {"type": "string"},
                "ctx_size": {"type": "integer"},
                "n_gpu_layers": {
                    "type": "integer",
                    "description": "GPU layers for action=settings (too high thrashs VRAM)",
                },
                "thinking": {
                    "type": "string",
                    "description": "on (default) | off — hidden reasoning for action=settings",
                },
                "enable_mtp": {
                    "type": "boolean",
                    "description": "Speculative MTP for action=settings (default on when GGUF is MTP)",
                },
                "n_cpu_moe": {
                    "type": "integer",
                    "description": "MoE experts on CPU (0 = auto from catalog) for action=settings",
                },
                "auto_start": {"type": "boolean"},
                "settings": {
                    "type": "object",
                    "description": (
                        "Partial rmb.json patch for action=settings. "
                        "thinking on|off (default on), enable_mtp, n_cpu_moe, "
                        "reasoning_budget, spec_draft_n_max, n_gpu_layers_draft, "
                        "model_draft, use_jinja, no_mmap, plus engine knobs."
                    ),
                },
                "load": {
                    "type": "boolean",
                    "description": "After pull, start RMB on that file (default true)",
                },
            },
        },
    )

