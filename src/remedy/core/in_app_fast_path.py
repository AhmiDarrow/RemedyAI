"""High-confidence in-app fast path — no provider tokens.

The hands (git, reminders, files, computer) already run on this PC. The
missing piece was *choosing* them: obvious asks still paid for a Claude /
GPT / DeepSeek / Gemini / OpenRouter / xAI round just to pick a tool.

This matcher covers the same class as the personal-assistant fast path, for
workspace and clock work that does not need a model. Unmatched or
multi-step → None (full ReAct, unchanged).
"""

from __future__ import annotations

import json
import re
from typing import Any

from remedy.assistant.fast_path import FastPathPlan
from remedy.core.build_oracle import coerce_text_arg

# Reject multi-clause / mutating VCS / reasoning even if a keyword matches.
_COMPLEX_RE = re.compile(
    r"(?is)\b("
    r"and then|after that|also |plus |but |should i|would you|"
    r"plan |help me (decide|figure)|compare |why |how (do|can|should) i|"
    r"write |email |draft |refactor|implement|debug|code |"
    r"commit|push|pull|checkout|rebase|merge|reset|stash|clone|"
    r"force.?push|amend"
    r")\b"
)

_GIT_STATUS_RE = re.compile(
    r"(?is)^\s*("
    r"git\s+status(\s+-sb)?"
    r"|what('?s| is) (the )?(git |repo |working.?tree )?status"
    r"|show (me )?(the )?(git )?status"
    r"|what changed"
    r"|what('?s| is) changed"
    r"|any (unstaged |uncommitted )?changes\??"
    r")\s*[.?!]?\s*$"
)

_GIT_DIFF_RE = re.compile(
    r"(?is)^\s*("
    r"git\s+diff"
    r"|show (me )?(the )?(git |unstaged |working.?tree )?diff"
    r"|what('?s| is) (the )?diff"
    r"|unstaged (changes|diff)"
    r"|show (me )?what changed"
    r")\s*[.?!]?\s*$"
)

_GIT_LOG_RE = re.compile(
    r"(?is)^\s*("
    r"git\s+log"
    r"|recent commits?"
    r"|show (me )?(the )?(recent )?commits?"
    r"|commit history"
    r"|what('?s| is) (the )?(recent )?commit history"
    r")\s*[.?!]?\s*$"
)

_REMINDER_LIST_RE = re.compile(
    r"(?is)^\s*("
    r"(list |show |get |check )?(my )?reminders?"
    r"|what (are|is) (on )?my reminders?"
    r"|what do (you|i) have (to remember|held)"
    r"|reminder_list"
    r")\s*[.?!]?\s*$"
)

# remind me in 30m to take out the trash
# remind me in 2 hours: call the pharmacy
_REMIND_ME_RE = re.compile(
    r"(?is)^\s*remind(?:\s+me)?\s+"
    r"(?:in\s+(?P<when>\d+\s*(?:minutes?|mins?|m|hours?|hrs?|h|days?|d))"
    r"|tomorrow(?:\s+(?P<tm>\d{1,2}(?::\d{2})?\s*(?:am|pm)?))?)"
    r"(?:\s+to|\s*[:—-])\s+"
    r"(?P<text>.+?)\s*$"
)


def match_in_app_fast_path(message: str) -> FastPathPlan | None:
    """Return a plan if *message* is a high-confidence local-tool request."""
    msg = coerce_text_arg(message)
    if not msg or len(msg) > 160:
        return None
    if _COMPLEX_RE.search(msg):
        return None
    if msg.count(".") + msg.count("?") + msg.count("!") > 1:
        return None
    if "\n" in msg:
        return None

    if _GIT_STATUS_RE.match(msg):
        return FastPathPlan("git_status", {}, "git_status")
    if _GIT_DIFF_RE.match(msg):
        return FastPathPlan("git_diff", {}, "git_diff")
    if _GIT_LOG_RE.match(msg):
        return FastPathPlan("git_log", {"limit": 20}, "git_log")
    if _REMINDER_LIST_RE.match(msg):
        return FastPathPlan("reminder_list", {}, "reminder_list")

    rm = _REMIND_ME_RE.match(msg)
    if rm:
        text = (rm.group("text") or "").strip(" .,-")
        if not text or len(text) > 120:
            return None
        when = (rm.group("when") or "").strip()
        if when:
            when = "in " + when
        else:
            tm = (rm.group("tm") or "").strip()
            when = f"tomorrow {tm}".strip() if tm else "tomorrow"
        return FastPathPlan(
            "remind_me",
            {"text": text, "when": when},
            "remind_me",
        )
    return None


def format_in_app_fast_path_reply(tool: str, raw: str) -> str:
    """Turn a local tool result into a short owner-facing reply (no LLM)."""
    text = (raw or "").strip()
    if not text:
        return "Done."

    if tool in ("git_status", "git_diff", "git_log"):
        return text if len(text) < 8000 else text[:8000] + "\n…"

    data: Any
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return text[:4000]
    if not isinstance(data, dict):
        return text[:4000]
    if data.get("ok") is False:
        return str(data.get("message") or "Could not complete that.")[:800]

    if tool == "reminder_list":
        items = data.get("reminders") or []
        if not items:
            return str(data.get("message") or "No reminders held.")
        lines = [f"**Reminders** ({data.get('count', len(items))}):"]
        for it in items[:25]:
            if not isinstance(it, dict):
                continue
            when = it.get("when") or it.get("due_iso") or "?"
            lines.append(f"- {when}: {it.get('text') or '(no text)'}")
        return "\n".join(lines)

    if tool == "remind_me":
        return str(data.get("message") or "Holding that.")

    if data.get("message"):
        return str(data["message"])
    return text[:2000]
