"""Attach built-in vision to computer-use screenshots.

User attachments already go through SmolVLM / native chat vision. Tool-taken
screenshots did not — pygame and other custom-drawn UIs came back as a path
and an empty UIA tree. This module:

1. Decodes the PNG with the local visual decoder (CUA click coords, or
   design critique when kind=design).
2. Queues the same PNG for native-vision chat models (Grok/Claude/GPT) so the
   next LLM step actually sees the pixels.
"""

from __future__ import annotations

import logging
from contextlib import suppress
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Focus question layered on the standard visual-decoder template.
CUA_FOCUS_QUESTION = (
    "This is a live computer-use screenshot. The agent must click and type "
    "on this screen (games, custom-drawn UIs, and apps with no accessibility tree).\n"
    "In addition to the usual sections, add:\n"
    "### Click targets (image pixels, origin = top-left of THIS image)\n"
    "For each visible control, sprite, button, menu, or text field, one line:\n"
    "- label: … | x: <int> | y: <int> | kind: button|text|sprite|menu|other\n"
    "x/y = visual center in THIS image. If unsure, estimate and say so.\n"
    "### Suggested next action\n"
    "One sentence the agent should do next (computer_click x= y= / computer_key / …)."
)

# Design observe: pixels only — no click-coordinate section.
DESIGN_FOCUS_QUESTION = (
    "This is a design-observe screenshot of a live UI. Critique only pixels "
    "you can actually see. Never invent a layout you cannot see.\n"
    "### Hierarchy\n"
    "What is primary vs secondary vs chrome? What draws the eye first?\n"
    "### Spacing / alignment / overflow\n"
    "Gaps, ragged edges, clipped or overflowing content, uneven columns.\n"
    "### Contrast / type vs taste\n"
    "Readability, type scale, contrast failures — not taste adjectives.\n"
    "### Empty / error / half-render chrome\n"
    "Blank panes, missing images, error toasts, loading skeletons stuck.\n"
    "### Top 3 pixel-backed fixes\n"
    "Three concrete file_edit-scale defects grounded in what is visible.\n"
    "### Cannot see\n"
    "Explicitly list anything you cannot see (off-screen, occluded, unreadably small).\n"
    "Do not invent a layout. Do not list click coordinates."
)

_MAX_QUEUED_SHOTS = 2


def _normalize_observe_kind(kind: str | None) -> str:
    k = (kind or "cua").strip().lower()
    return k if k == "design" else "cua"


def focus_question_for_kind(kind: str = "cua", hint: str = "") -> str:
    base = (
        DESIGN_FOCUS_QUESTION
        if _normalize_observe_kind(kind) == "design"
        else CUA_FOCUS_QUESTION
    )
    if (hint or "").strip():
        return f"{base}\n\nTask hint: {hint.strip()[:400]}"
    return base


def queue_native_screenshot(
    runtime: Any,
    path: str | Path,
    *,
    origin: dict[str, Any] | None = None,
    width: int | None = None,
    height: int | None = None,
    kind: str = "cua",
) -> None:
    """Remember a shot so a native-vision chat model can see it next step."""
    if runtime is None:
        return
    p = str(path or "").strip()
    if not p or not Path(p).is_file():
        return
    sid = "_"
    try:
        from remedy.core.turn_context import turn_session_id

        sid = str(turn_session_id(runtime) or "").strip() or "_"
    except Exception:
        sid = "_"
    buckets = getattr(runtime, "_pending_cua_shots_by_sid", None)
    if not isinstance(buckets, dict):
        buckets = {}
        runtime._pending_cua_shots_by_sid = buckets
    lst = buckets.setdefault(sid, [])
    # Keep a legacy alias for the active session (tests / single-tab)
    runtime._pending_cua_shots = lst
    lst.append(
        {
            "path": p,
            "origin": dict(origin or {}),
            "width": width,
            "height": height,
            "kind": _normalize_observe_kind(kind),
        }
    )
    if len(lst) > _MAX_QUEUED_SHOTS:
        del lst[:-_MAX_QUEUED_SHOTS]


def chat_supports_native_vision(runtime: Any = None) -> bool:
    try:
        from remedy.core.llm_binding import get_llm_binding
        from remedy.interfaces.config import load_config
        from remedy.vision.capabilities import resolve_supports_vision

        cfg = load_config() or {}
        b = get_llm_binding(runtime)
        return bool(
            resolve_supports_vision(b.provider, b.model, config=cfg)
        )
    except Exception:
        return False


def flush_native_screenshots(runtime: Any) -> dict[str, Any] | None:
    """If the chat model can see images, emit one multimodal user message."""
    sid = "_"
    try:
        from remedy.core.turn_context import turn_session_id

        sid = str(turn_session_id(runtime) or "").strip() or "_"
    except Exception:
        sid = "_"
    buckets = getattr(runtime, "_pending_cua_shots_by_sid", None)
    if isinstance(buckets, dict):
        shots = list(buckets.pop(sid, None) or [])
    else:
        shots = list(getattr(runtime, "_pending_cua_shots", None) or [])
    if runtime is not None:
        runtime._pending_cua_shots = []
    if not shots:
        return None
    if not chat_supports_native_vision(runtime):
        return None
    from remedy.vision.decoder import _image_data_url

    parts: list[dict[str, Any]] = []
    notes: list[str] = []
    used_kinds: list[str] = []
    for shot in shots[-_MAX_QUEUED_SHOTS:]:
        p = Path(str(shot.get("path") or ""))
        if not p.is_file():
            continue
        data_url = _image_data_url(p, 4 * 1024 * 1024)
        if not data_url:
            continue
        origin = shot.get("origin") or {}
        notes.append(
            f"{p.name} {shot.get('width') or '?'}x{shot.get('height') or '?'} "
            f"origin=({origin.get('x', 0)},{origin.get('y', 0)})"
        )
        used_kinds.append(_normalize_observe_kind(str(shot.get("kind") or "cua")))
        parts.append({"type": "image_url", "image_url": {"url": data_url}})
    if not parts:
        return None
    note_s = " | ".join(notes)
    if used_kinds and all(k == "design" for k in used_kinds):
        header = (
            "[Design screenshot — you can see this image.]\n"
            "Critique visible pixels. file_edit only listed defects. "
            "Do not computer_click. Never invent a layout you cannot see. "
            + note_s
        )
    else:
        header = (
            "[Computer screenshot — you can see this image.]\n"
            "Click with computer_click x= y= using pixels from the **top-left of "
            "this image**, then add origin offset if origin ≠ (0,0).\n"
            "Prefer these pixels over guessing. "
            + note_s
        )
    return {
        "role": "user",
        "content": [{"type": "text", "text": header}, *parts],
    }


def decode_screenshot_brief(
    path: str | Path,
    *,
    extra_question: str | None = None,
    timeout_s: float = 25.0,
    allow_cpu_wake: bool = False,
) -> dict[str, Any]:
    """Run the local visual decoder. Never raises. ok=False if unavailable."""
    p = Path(path)
    out: dict[str, Any] = {"ok": False, "text": "", "error": ""}
    if not p.is_file():
        out["error"] = "screenshot missing"
        return out
    try:
        from remedy.interfaces.config import load_config
        from remedy.vision.service import get_status

        cfg = load_config() or {}
        st = get_status(cfg, light=True)
        # Click path must not cold-start (minutes). Design observe may wake a
        # CPU-only decoder so RMB can keep the GPU.
        if not st.get("running") and allow_cpu_wake:
            with suppress(Exception):
                from remedy.vision.runtime import start_server

                start_server(n_gpu_layers=0, ignore_rmb=True, wait_s=20.0)
                st = get_status(cfg, light=True)
        if not st.get("running"):
            out["error"] = (
                "local decoder idle (not running). Native chat vision still "
                "receives the PNG. Use vision_decode action=decode to start "
                "SmolVLM, or Settings → Local model auto-start."
            )
            return out
        base = str(st.get("base_url") or "")
        if not base:
            out["error"] = "no vision base_url"
            return out
        from remedy.vision.decoder import decode_image

        q = (extra_question or "").strip() or CUA_FOCUS_QUESTION
        result = decode_image(
            p,
            base_url=base,
            timeout_s=float(timeout_s),
            extra_question=q,
        )
        if result.get("ok") and result.get("text"):
            out["ok"] = True
            out["text"] = str(result.get("text") or "")
            return out
        out["error"] = str(result.get("error") or "decode returned empty")
        return out
    except Exception as e:
        logger.debug("computer vision decode failed: %s", e)
        out["error"] = str(e)
        return out


def observe_screenshot(
    path: str | Path,
    *,
    runtime: Any = None,
    origin: dict[str, Any] | None = None,
    width: int | None = None,
    height: int | None = None,
    hint: str = "",
    kind: str = "cua",
) -> dict[str, Any]:
    """Decode + queue native. Safe to call on the tool thread."""
    k = _normalize_observe_kind(kind)
    q = focus_question_for_kind(k, hint)
    decoded = decode_screenshot_brief(
        path, extra_question=q, allow_cpu_wake=(k == "design")
    )
    queue_native_screenshot(
        runtime, path, origin=origin, width=width, height=height, kind=k
    )
    return decoded


def format_vision_block(
    decoded: dict[str, Any],
    *,
    origin: dict[str, Any] | None = None,
    path: str = "",
) -> str:
    """Text appended to the computer tool result for the chat model."""
    ox = (origin or {}).get("x", 0)
    oy = (origin or {}).get("y", 0)
    if decoded.get("ok") and decoded.get("text"):
        return (
            "## Visual decode (built-in local vision)\n"
            f"{decoded['text']}\n"
            f"Screen origin offset of this capture: ({ox}, {oy}). "
            "computer_click x= y= uses **screen** pixels: "
            "x_screen = x_image + origin.x, y_screen = y_image + origin.y.\n"
            "If UIA/DOM refs were empty, trust this decode and click by coordinates."
        )
    err = str(decoded.get("error") or "decoder unavailable")
    native = " Chat model may still see the PNG on the next step." if path else ""
    return (
        "## Visual decode skipped\n"
        f"({err}){native}\n"
        f"Screenshot file: {path or '(none)'}\n"
        "Retry vision_decode action=decode on that path, or computer_click x/y "
        "if you can see the image."
    )


def snapshot_needs_vision(
    elements: list[dict[str, Any]] | None,
    *,
    hint: str = "",
    last_target: str = "",
    already_fallback: bool = False,
) -> bool:
    """True when UIA/DOM is too thin to drive (typical pygame / custom paint)."""
    if already_fallback:
        return False
    els = list(elements or [])
    n_c = sum(1 for e in els if str(e.get("ref") or "").lower().startswith("c"))
    n_e = sum(1 for e in els if str(e.get("ref") or "").lower().startswith("e"))
    if n_c or n_e:
        return False
    blob = (hint or "").lower()
    if any(k in blob for k in ("game", "pygame", "play", "sdl", "sprite")):
        return True
    # After computer_app / desktop drive, empty controls → see the pixels
    return (last_target or "").strip().lower() == "desktop"
