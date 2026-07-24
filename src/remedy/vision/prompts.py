"""Fixed multi-task prompts for the visual decoder VLM."""

from __future__ import annotations

DECODE_SYSTEM = (
    "You are a visual decoder for an AI agent. Describe images accurately and "
    "completely. Prefer facts over speculation. If text is unreadable, say so. "
    "Do not role-play as the main assistant — only report what is visible."
)

DECODE_USER_TEMPLATE = """Analyze this image for an AI agent that cannot see pixels.
Cover ALL of the following sections using this exact markdown structure:

### Visual decode: {filename}
- **Scene:** (1-3 sentences: overall content, setting, style)
- **OCR / readable text:** (transcribe visible text; preserve layout roughly; use "(none)" if no text)
- **UI / layout notes:** (windows, buttons, dialogs, code panes, error chrome — or "n/a")
- **Notable objects / design cues:** (subjects, colors, brand marks, charts — concise bullets ok)
- **Confidence / caveats:** (blur, crop, uncertain text)

Be thorough on OCR for screenshots and documents. Keep under ~400 words unless dense text requires more.
"""


def decode_user_prompt(filename: str, extra_question: str | None = None) -> str:
    base = DECODE_USER_TEMPLATE.format(filename=filename or "image")
    q = (extra_question or "").strip()
    if q:
        base += f"\n\nAdditional focus question from the agent:\n{q}\n"
    return base
