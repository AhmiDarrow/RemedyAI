"""Speakable owner sentences — Yes, No, or Explain. No tool names.

Approvals, progress, and stuck states for life tasks (and any checkpoint)
must be one sentence a non-technical or low-vision owner can hear.
"""

from __future__ import annotations

import re

from remedy.core.build_oracle import coerce_text_arg

_TOOLISH = re.compile(
    r"(?i)\b(computer_\w+|life_drive|bash_exec|host_run|file_\w+|job_run)\b"
)

_CHOICES = "Yes, No, or Explain?"


def strip_tool_names(text: str) -> str:
    """Drop function names so speech never says computer_click."""
    return _TOOLISH.sub("that step", coerce_text_arg(text)).strip()


def speakable_plan(goal: str, step_titles: list[str], *, stops: list[str] | None = None) -> str:
    """One-sentence plan plus the three choices."""
    g = coerce_text_arg(goal) or "your goal"
    titles = [coerce_text_arg(t) for t in step_titles if coerce_text_arg(t)]
    body = ", then ".join(titles[:8]) if titles else "the next steps on this computer"
    stop = ""
    if stops:
        stop = " Then Remedy will stop so you can handle: " + ", ".join(
            coerce_text_arg(s) for s in stops if coerce_text_arg(s)
        ) + "."
    sentence = f"Remedy will work toward {g}: {body}.{stop} {_CHOICES}"
    return strip_tool_names(sentence)


def speakable_progress(step: int, total: int, title: str) -> str:
    t = coerce_text_arg(title) or "the next step"
    return strip_tool_names(
        f"Step {max(1, step)} of {max(step, total)} — {t}."
    )


def speakable_checkpoint(title: str) -> str:
    t = coerce_text_arg(title) or "this irreversible step"
    return strip_tool_names(
        f"Remedy stopped. This needs you: {t}. Nothing was sent or paid. {_CHOICES}"
    )


def speakable_blocked(title: str, reason: str = "") -> str:
    t = coerce_text_arg(title) or "that step"
    why = coerce_text_arg(reason)
    extra = f" Because: {why}." if why else ""
    return strip_tool_names(
        f"Remedy could not finish {t}.{extra} {_CHOICES}"
    )


def speakable_done(goal: str) -> str:
    g = coerce_text_arg(goal) or "the goal"
    return strip_tool_names(
        f"Done — {g} finished, and each step was checked on screen."
    )


def explain_plan(goal: str, markdown: str) -> str:
    """Longer Explain answer: the saved trail, still without tool names."""
    g = coerce_text_arg(goal) or "this task"
    body = strip_tool_names(markdown or "")
    if not body:
        body = "No further detail is saved yet."
    return f"Explain — toward {g}:\n{body}"
