"""Tools: companion_context, clipboard_read, clipboard_write, companion_design."""

from __future__ import annotations

from typing import Any


def register_companion_tools(runtime: Any) -> None:
    """Register personal-PC companion tools (clipboard, foreground, design pass)."""

    async def companion_context() -> str:
        """What the owner is looking at and holding (foreground + clipboard + recent)."""
        from remedy.core.companion import format_companion_block, gather_companion_snapshot

        snap = gather_companion_snapshot(runtime)
        return format_companion_block(snap)

    async def clipboard_read() -> str:
        """Read the OS clipboard (text, image path, or file list)."""
        from remedy.core.companion import gather_companion_snapshot

        snap = gather_companion_snapshot(runtime, include_recent=False)
        clip = snap.get("clipboard") or {}
        kind = clip.get("kind") or "empty"
        if kind == "text":
            body = str(clip.get("preview") or "")
            note = "\n…[truncated]" if clip.get("truncated") else ""
            return f"clipboard text ({clip.get('chars')} chars)\n{body}{note}"
        if kind == "image":
            path = str(clip.get("path") or "")
            from remedy.core.companion_observe import design_observe_path

            res = design_observe_path(
                runtime, path, hint="clipboard image", via="clipboard"
            )
            md = str(res.get("image_md") or "")
            extra = f"\n{md}" if md else ""
            return (
                f"clipboard image saved to {path}\n"
                f"{res.get('message')}{extra}\n"
                "Do not ask what they copied."
            )
        if kind == "files":
            paths = "\n".join(f"- {p}" for p in (clip.get("paths") or []))
            return f"clipboard files:\n{paths}"
        return "clipboard is empty"

    async def clipboard_write(text: str = "") -> str:
        """Copy text onto the OS clipboard so the owner can paste it anywhere."""
        from remedy.core.companion import get_companion_backend

        body = text if isinstance(text, str) else str(text or "")
        if not body.strip():
            return "text= required (nothing to copy)"
        ok = bool(get_companion_backend().set_clipboard_text(body))
        if not ok:
            return "clipboard_write failed (not Windows, or clipboard locked)"
        return f"Copied {len(body)} chars to the clipboard. Owner can paste now."

    async def companion_design(goal: str = "") -> str:
        """Design pass: gather screen/clipboard evidence + seed a critique checklist."""
        from remedy.core.companion import design_pass, format_companion_block
        from remedy.core.companion_observe import design_observe_paths

        res = design_pass(runtime, goal=goal)
        block = format_companion_block(res.get("snapshot") or {})
        ev = res.get("evidence") or []
        ev_s = "\n".join(f"- {e}" for e in ev) or "- (no image/file yet — computer_screenshot if needed)"
        seen = design_observe_paths(runtime, ev, hint=goal)
        briefs = "\n\n".join(
            "\n".join(
                x
                for x in (
                    s.get("message") or "",
                    s.get("image_md") or "",
                )
                if x
            )
            for s in seen
        )
        extra = f"\n\n{briefs}" if briefs else ""
        return (
            f"{res.get('message')}\n\n{block}\n\nEvidence:\n{ev_s}{extra}\n\n"
            "Next: structured critique (goal, hierarchy, usability, top 3), "
            "then implement in real files and re-observe."
        )

    runtime.tool_registry.register_builtin_handler(
        "companion_context",
        "Personal PC snapshot: focused window, clipboard (text/image/files), "
        "and recent Desktop/Downloads/Documents. Use when the user says look "
        "at this / what's on my screen / I copied / design this. Do not ask "
        "what they copied if the clipboard already has it.",
        companion_context,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "clipboard_read",
        "Read the OS clipboard. Text, saved image path, or copied file list.",
        clipboard_read,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "clipboard_write",
        "Copy text to the OS clipboard so the owner can paste into any app.",
        clipboard_write,
        {
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "companion_design",
        "Design colleague pass: gather clipboard/screen/recent visual evidence "
        "and seed a critique→make→re-observe checklist. Prefer this over a "
        "vague 'make it pop' monologue.",
        companion_design,
        {
            "type": "object",
            "properties": {"goal": {"type": "string"}},
            "required": [],
        },
    )

    async def companion_observe() -> str:
        """Screenshot the focused window / desktop after a UI change."""
        from remedy.core.companion_observe import (
            capture_foreground_png,
            design_observe_path,
        )

        cap = capture_foreground_png(runtime)
        if not cap.get("ok"):
            return (
                f"observe MISS via={cap.get('via')}: {cap.get('error')}\n"
                "You cannot see the UI yet. Do not draw ASCII or box layouts."
            )
        res = design_observe_path(
            runtime,
            str(cap.get("path") or ""),
            via=str(cap.get("via") or ""),
        )
        md = str(res.get("image_md") or "")
        extra = f"\n{md}" if md else ""
        return (
            f"observe OK via={res.get('via')} path={res.get('path')}\n"
            f"{res.get('message')}{extra}"
        )

    async def companion_taste(fact: str = "") -> str:
        """Show or remember durable design taste (spacing, type, density)."""
        from remedy.core.companion_taste import (
            format_taste_block,
            load_taste,
            remember_taste,
        )

        if (fact or "").strip():
            row = remember_taste(fact.strip(), runtime)
            return f"Remembered taste `{row.get('id')}`: {row.get('fact')}"
        return format_taste_block(load_taste(runtime)) or "No taste facts yet."

    async def companion_inbox() -> str:
        """New Desktop/Downloads drops since last poll."""
        from remedy.core.companion_inbox import format_inbox_block, poll_new_drops

        drops = poll_new_drops(runtime)
        return format_inbox_block(drops) or "No new drops."

    runtime.tool_registry.register_builtin_handler(
        "companion_observe",
        "Capture the focused window or desktop after a UI write. Green tests "
        "do not prove the window — look, then file_edit what is wrong.",
        companion_observe,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "companion_taste",
        "Durable design taste. fact= to remember (e.g. '8px spacing, Inter, dark'). "
        "Empty fact= lists stored taste. Honored on every design pass.",
        companion_taste,
        {
            "type": "object",
            "properties": {"fact": {"type": "string"}},
            "required": [],
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "companion_inbox",
        "List new files on Desktop/Downloads that appeared since last look "
        "(mocks, logs, zips). Use when the owner may have dropped a file.",
        companion_inbox,
        {"type": "object", "properties": {}},
    )
