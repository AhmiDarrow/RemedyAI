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
        """
        from remedy.core.local_discover import (
            collect_skill_local_specs,
            discover_all,
            discover_one,
        )

        act = (action or "scan").strip().lower()
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
                        md = comfy.markdown_for_image(
                            meta, caption=text[:80], embed_data_uri=True
                        )
                        blocks.append(md)
                        image_blocks.append(md)
                        runtime._track_artifact(str(meta.get("path") or img_path))
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

