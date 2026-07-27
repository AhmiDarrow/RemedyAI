"""Intent → policy packs — reshape the envelope without multi-agent theater.

The frontier model still speaks as Remedy. Policy packs inject short system
guidance and (optionally) bias tool use. Deterministic; no network.
"""

from __future__ import annotations

from typing import Any

# Compact system addenda — never rewrite persona; only focus the turn.
_PACKS: dict[str, dict[str, Any]] = {
    "chat": {
        "id": "chat",
        "system": "",
        "prefer_tools": False,
    },
    "memory": {
        "id": "memory",
        "system": (
            "[Continuity] User is asking about durable knowledge or recall. "
            "Prefer memory_search / profile facts already in context before guessing. "
            "If unknown, say so and offer to remember."
        ),
        "prefer_tools": True,
        "suggest_tools": ["memory_search"],
    },
    "skill": {
        "id": "skill",
        "system": (
            "[Continuity] User wants a procedure or skill. Use skill_search / skill_activate "
            "from the catalog — do not invent skill names. Prefer proven skills when ranked."
        ),
        "prefer_tools": True,
        "suggest_tools": ["skill_search", "skill_activate"],
    },
    "plan": {
        "id": "plan",
        "system": (
            "[Continuity] Planning mode bias: outline steps, risks, and decision points. "
            "Avoid destructive tools until the plan is clear; use plan tools if available."
        ),
        "prefer_tools": False,
        "suggest_tools": ["plan_list", "plan_show"],
    },
    "tool": {
        "id": "tool",
        "system": (
            "[Continuity] User wants work done on the machine or project. "
            "Prefer tools over monologue: file_edit (multi-hunk via edits=) for "
            "precise edits, repo_search for discovery (absolute path= when multi-tree), "
            "file_write for new files, bash_exec with timeout_seconds/workdir for builds. "
            "Use job_run explore/verify/diff for surveys. Keep going until finished."
        ),
        "prefer_tools": True,
        "suggest_tools": ["file_edit", "repo_search", "file_read", "bash_exec", "job_run"],
    },
    "autonomous": {
        "id": "autonomous",
        "system": (
            "[Continuity · Work alone] The user stepped away or asked you to handle "
            "this end-to-end without check-ins. Act with high agency:\n"
            "1) mission_start with goal, checklist steps, and verify_command "
            "(stack fingerprint may auto-fill; e.g. pytest -q or Godot headless smoke).\n"
            "2) Prefer file_edit / multi-hunk edits=; repo_search for discovery; "
            "file_write only for new files. Use absolute paths when juggling trees.\n"
            "3) job_run kind=explore to survey; kind=verify for tests; kind=diff for git.\n"
            "4) mission_update after each step; mission_verify before claiming done "
            "(do not claim complete if verify failed).\n"
            "5) On failure: read errors (path:line), file_edit fixes, re-verify "
            "(raise timeout_seconds for long builds).\n"
            "Do not stop at a question when you can pick a reasonable default. "
            "Escalate only for secrets, paid APIs, or irreversible destroy. "
            "Summarize when finished."
        ),
        "prefer_tools": True,
        "suggest_tools": [
            "mission_start",
            "mission_update",
            "mission_verify",
            "file_edit",
            "repo_search",
            "job_run",
            "bash_exec",
        ],
    },
}


def policy_for_intent(intent: str, *, user_text: str = "") -> dict[str, Any]:
    """Return a policy pack for an intent label."""
    key = (intent or "chat").strip().lower()
    pack = dict(_PACKS.get(key) or _PACKS["chat"])
    # Light boosts from raw text when classifier is generic
    ut = (user_text or "").lower()
    # Autonomous / work-alone always wins when phrased clearly
    if any(
        p in ut
        for p in (
            "work alone",
            "on your own",
            "handle this on your own",
            "i need to go",
            "be with my kids",
            "step away",
            "don't wait for me",
            "do not wait for me",
            "unattended",
            "fully autonomous",
            "finish without me",
            "take it from here",
            "you got this",
        )
    ):
        return dict(_PACKS["autonomous"])
    if key == "chat":
        if any(w in ut for w in ("remember", "what do you know", "/memory", "/whoami", "/forget")):
            return dict(_PACKS["memory"])
        if any(w in ut for w in ("skill", "/skills", "procedure")):
            return dict(_PACKS["skill"])
        if any(w in ut for w in ("plan", "roadmap", "break down")):
            return dict(_PACKS["plan"])
        if any(
            w in ut
            for w in (
                "implement",
                "fix",
                "debug",
                "run ",
                "create file",
                "write a",
                "edit ",
                "refactor",
            )
        ):
            return dict(_PACKS["tool"])
    if key == "tool" and any(
        p in ut for p in ("alone", "on your own", "without me", "end-to-end", "end to end")
    ):
        return dict(_PACKS["autonomous"])
    return pack


def format_policy_block(pack: dict[str, Any] | None) -> str:
    if not pack:
        return ""
    text = str(pack.get("system") or "").strip()
    tools = pack.get("suggest_tools") or []
    if tools and text:
        text += " Suggested tools when needed: " + ", ".join(str(t) for t in tools) + "."
    return text
