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

_TODO_READ_RE = re.compile(
    r"(?is)^\s*("
    r"(show |list |get |read )?(my |the |active )?(todos?|to-?dos?|checklist)( list)?"
    r"|what('?s| is) on (the |my )?(todo list|checklist)"
    r"|todo_read"
    r")\s*[.?!]?\s*$"
)

# Cwd only — a path like "list files in src" stays on the provider.
_LIST_DIR_RE = re.compile(
    r"(?is)^\s*("
    r"ls"
    r"|dir"
    r"|list_dir"
    r"|(list|show|display) (me )?(the |all )?(files?|dirs?|directories|folders?)"
    r"( here| in this (folder|directory|project))?"
    r"|what('?s| is) in (this |the )?(folder|directory|project|cwd)"
    r")\s*[.?!]?\s*$"
)

_VERIFY_RE = re.compile(
    r"(?is)^\s*("
    r"(please )?(run|execute) (the |all |my )?(tests?|pytest|test suite|unit tests)"
    r"|pytest( -q)?"
    r")\s*[.?!]?\s*$"
)

_CLIPBOARD_RE = re.compile(
    r"(?is)^\s*("
    r"what('?s| is) (on )?(the |my )?clipboard"
    r"|read (the |my )?clipboard"
    r"|clipboard_read"
    r")\s*[.?!]?\s*$"
)

_WHICH_RE = re.compile(
    r"(?is)^\s*(which|where is|where'?s)\s+"
    r"(?P<name>python3?|py|git|node|npm|pnpm|yarn|pwsh|powershell|"
    r"uv|cargo|pytest|rg|gh|pip3?|go|rustc|cmake)"
    r"\s*\??\s*$"
)

_GOAL_LIST_RE = re.compile(
    r"(?is)^\s*("
    r"(list|show) (my |the )?(open |life )?goals?"
    r"|what (are|is) (my )?(open )?goals?"
    r"|goal_list"
    r")\s*[.?!]?\s*$"
)

_SHIP_STATUS_RE = re.compile(
    r"(?is)^\s*("
    r"ship[_ ]status"
    r"|what('?s| is) (the )?ship status"
    r"|show (me )?(the )?ship status"
    r")\s*[.?!]?\s*$"
)

_MISSION_STATUS_RE = re.compile(
    r"(?is)^\s*("
    r"mission[_ ]status"
    r"|show (me )?(the |my )?(active )?mission( status)?"
    r"|what('?s| is) (the |my )?(active )?mission status"
    r")\s*[.?!]?\s*$"
)

_SCREEN_RE = re.compile(
    r"(?is)^\s*("
    r"what('?s| is) on (my |the )?(screen|display|monitor)"
    r"|what am i looking at"
    r"|what('?s| is) (the )?(focused|foreground|active) window"
    r"|look at (my |the )?screen"
    r"|companion_context"
    r")\s*[.?!]?\s*$"
)

_PLAIN_TOOLS = frozenset(
    {
        "git_status",
        "git_diff",
        "git_log",
        "todo_read",
        "list_dir",
        "job_run",
        "clipboard_read",
        "host_which",
        "goal_list",
        "ship_status",
        "mission_status",
        "companion_context",
    }
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
    if _TODO_READ_RE.match(msg):
        return FastPathPlan("todo_read", {}, "todo_read")
    if _LIST_DIR_RE.match(msg):
        return FastPathPlan("list_dir", {"path": "."}, "list_dir")
    if _VERIFY_RE.match(msg):
        return FastPathPlan("job_run", {"kind": "verify"}, "job_verify")
    if _CLIPBOARD_RE.match(msg):
        return FastPathPlan("clipboard_read", {}, "clipboard_read")
    which = _WHICH_RE.match(msg)
    if which:
        name = (which.group("name") or "").strip()
        if name:
            return FastPathPlan("host_which", {"name": name}, "host_which")
    if _GOAL_LIST_RE.match(msg):
        return FastPathPlan("goal_list", {}, "goal_list")
    if _SHIP_STATUS_RE.match(msg):
        return FastPathPlan("ship_status", {}, "ship_status")
    if _MISSION_STATUS_RE.match(msg):
        return FastPathPlan("mission_status", {}, "mission_status")
    if _SCREEN_RE.match(msg):
        return FastPathPlan("companion_context", {}, "companion_context")
    return None


def format_in_app_fast_path_reply(tool: str, raw: str) -> str:
    """Turn a local tool result into a short owner-facing reply (no LLM)."""
    text = (raw or "").strip()
    if not text:
        return "Done."

    if tool in _PLAIN_TOOLS:
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
