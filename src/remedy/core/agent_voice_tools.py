"""Her voice identity, from the conversation.

One voice. It carries a reference and four numbers (pace, pitch, warmth,
articulation) that move in small, clamped, journaled steps — never a jump
to someone else. These tools let her adjust herself when the owner asks
("a touch slower", "a little brighter") and undo it ("sound like you did
before").
"""

from __future__ import annotations

from typing import Any

from remedy.core.errors import format_tool_error

# One nudge = one small step. The clamps in voice.identity bound the total.
_STEP = {
    "pace": 0.03,
    "pitch_semitones": 0.25,
    "warmth": 0.06,
    "articulation": 0.06,
}


def register_voice_tools(runtime: Any) -> None:
    def _home() -> str | None:
        cfg = getattr(runtime, "config", None)
        if isinstance(cfg, dict) and cfg.get("home_dir"):
            return str(cfg["home_dir"])
        if cfg is not None:
            h = getattr(cfg, "home_dir", None)
            if h:
                return str(h)
        h2 = getattr(runtime, "home", None)
        return str(h2) if h2 else None

    def _describe(ident: Any) -> str:
        return (
            f"pace {ident.pace:.2f}, pitch {ident.pitch_semitones:+.2f} st, "
            f"warmth {ident.warmth:.2f}, articulation {ident.articulation:.2f}"
        )

    async def voice_identity() -> str:
        try:
            from remedy.voice.identity import load

            ident = load(_home())
        except Exception as e:
            return format_tool_error(str(e), code="VOICE_IDENTITY", tool_name="voice_identity")
        ref = "own reference clip" if ident.reference_wav else "the built-in reference"
        steps = len([j for j in ident.journal if j.get("change") == "evolve"])
        return f"My voice: {_describe(ident)}; {ref}; {steps} adjustment(s) so far."

    async def voice_adjust(
        pace: int = 0,
        pitch: int = 0,
        warmth: int = 0,
        articulation: int = 0,
    ) -> str:
        """Nudge one or more traits by whole steps (−3..+3 each)."""
        try:
            from remedy.voice.identity import evolve

            def clamp(n: Any) -> int:
                return max(-3, min(3, int(n or 0)))

            ident = evolve(
                _home(),
                pace=clamp(pace) * _STEP["pace"] or None,
                pitch_semitones=clamp(pitch) * _STEP["pitch_semitones"] or None,
                warmth=clamp(warmth) * _STEP["warmth"] or None,
                articulation=clamp(articulation) * _STEP["articulation"] or None,
            )
        except Exception as e:
            return format_tool_error(str(e), code="VOICE_ADJUST", tool_name="voice_adjust")
        return f"Adjusted. My voice is now {_describe(ident)}. It takes effect on my next reply."

    async def voice_revert(steps: int = 1) -> str:
        """Undo the last N adjustments (default 1)."""
        try:
            from remedy.voice.identity import revert

            ident = revert(_home(), steps=max(1, int(steps or 1)))
        except Exception as e:
            return format_tool_error(str(e), code="VOICE_REVERT", tool_name="voice_revert")
        return f"Reverted. My voice is now {_describe(ident)}."

    runtime.tool_registry.register_builtin_handler(
        "voice_identity",
        "How my voice is currently set (pace, pitch, warmth, articulation) and "
        "how many times it has been adjusted. Use before adjusting.",
        voice_identity,
        {"type": "object", "properties": {}},
    )
    runtime.tool_registry.register_builtin_handler(
        "voice_adjust",
        "Adjust my voice a little when the owner asks (e.g. 'a touch slower', "
        "'a bit brighter', 'warmer', 'crisper'). Each trait moves by whole "
        "steps from -3 to +3; one step is small and the total is bounded, so "
        "I stay recognisably me. pace: -=slower +=quicker. pitch: -=lower "
        "+=higher. warmth: -=leaner +=fuller. articulation: -=steadier +=livelier.",
        voice_adjust,
        {
            "type": "object",
            "properties": {
                "pace": {"type": "integer", "minimum": -3, "maximum": 3},
                "pitch": {"type": "integer", "minimum": -3, "maximum": 3},
                "warmth": {"type": "integer", "minimum": -3, "maximum": 3},
                "articulation": {"type": "integer", "minimum": -3, "maximum": 3},
            },
        },
    )
    runtime.tool_registry.register_builtin_handler(
        "voice_revert",
        "Undo my last voice adjustment(s) when the owner preferred how I sounded before.",
        voice_revert,
        {"type": "object", "properties": {"steps": {"type": "integer", "minimum": 1}}},
    )
