"""Intent → policy packs — reshape the envelope without multi-agent theater.

The frontier model still speaks as Remedy. Policy packs inject short system
guidance and (optionally) bias tool use. Deterministic; no network.
"""

from __future__ import annotations

import re
from typing import Any

# Read-only intents (review / analyze / explain) must NOT be force-fit into the
# write-and-verify build loop — that is what strands "review the project" in an
# endless RESEARCH→PLAN→BUILD spiral. This detector is shared with the build
# engine so the classifier and the machine supervisor agree on "read-only".
# Strong read-only framings — inherently "tell me / show me", never "do it".
# These win even when a change word appears later (usually as a noun, e.g.
# "explain how the build loop works").
_STRONG_READONLY_RE = re.compile(
    r"(?i)("
    r"\b(explain|describe|summar(?:y|ize|ise)|walk\s+me\s+through|"
    r"tell\s+me\s+(?:about|how|what|why))\b"
    # "what does/is/are X" but not social ("what's up/new/good", "what are you")
    r"|\bwhat\s+(?:does|is|are)\s+(?!you\b|up\b|new\b|good\b|going\s+on)"
    r"|\bhow\s+(?:does|do)\b"
    # "how is/are X" but not "how are you / how's it going / how are things"
    r"|\bhow\s+(?:is|are)\s+(?!you\b|it\s+going|things\b|ya\b|we\b|everyone\b|your\s+day)"
    r"|\bwhy\s+(?:is|does|are|do)\s+(?!you\b)"
    r")"
)
# Weak read-only verbs — read-only only when no change verb is also present.
_WEAK_READONLY_RE = re.compile(
    r"(?i)\b("
    r"review|audit|analy[sz]e|analysis|assess|examine|inspect|"
    r"investigate|research|go\s+over|read\s+through|understand|"
    r"look\s+(?:over|at|into|through)|critique|evaluate|diagnose|trace"
    r")\b"
)
# Change verbs exclude read-only. "build"/"release"/"ship"/"patch" are pinned to
# their verb sense so noun phrases ("the build system", "release notes") don't
# trip. Anything missed here is still caught by the write→build auto-upgrade.
_CHANGE_VERB_RE = re.compile(
    r"(?i)\b("
    r"implement|scaffold|deploy|rebuild|"
    r"build(?:s|ing)?\s+(?:a|an|the|me|us|it|this|out|from|your|my|new)|"
    r"ship\s+(?:it|a|the)|releas(?:e|es|ing|ed)\s+(?:a|an|the|it|this|version|v?\d)|"
    r"creat(?:e|ing)|generate|writ(?:e|ing)\s+(?:a|the|some|me)?\s*"
    r"(?:script|file|test|code|module|app|program|function|class|page|patch)|"
    r"add(?:s|ing)?|fix(?:es|ing)?|refactor|edit(?:s|ing)?|modif(?:y|ies|ying)|"
    r"chang(?:e|ing)|updat(?:e|ing)|rewrit(?:e|ing)|patch\s+(?:it|the|a)|"
    r"migrat(?:e|ing)|upgrad(?:e|ing)|replac(?:e|ing)|install|delet(?:e|ing)|"
    r"remov(?:e|ing)|renam(?:e|ing)|convert|optimi[sz]e|wire\s+up|"
    r"set\s*up|setup|make\s+(?:it|me|a|the)|bump"
    r")\b"
)


def looks_like_readonly_request(message: str) -> bool:
    """True when the ask is read-only (review/analyze/explain) with no change verb.

    Two-tier: a strong framing ("explain/summarize/what does…") is read-only even
    if a change word appears (usually a noun). A weak verb (review/audit/analyze…)
    is read-only only when no change verb is present. Conservative so genuine build
    work keeps the full research → plan → build loop — and the build engine upgrades
    a read-only turn to a full build the instant the model writes a file, so a
    misclassified edit is never stranded.
    """
    msg = (message or "").strip()
    if not msg:
        return False
    if _STRONG_READONLY_RE.search(msg):
        return True
    if _CHANGE_VERB_RE.search(msg):
        return False
    return bool(_WEAK_READONLY_RE.search(msg))


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
            "Prefer soul_recall (unified Soul + Crystal + Partner Memory), then "
            "memory_search / profile facts already in context before guessing. "
            "If unknown, say so and offer to remember. soul_status for personhood state."
        ),
        "prefer_tools": True,
        "suggest_tools": ["soul_recall", "soul_status", "memory_search"],
    },
    "task": {
        "id": "task",
        "system": (
            "[Work] Tools until the goal is done. Don't write a plan and stop. "
            "Don't claim finished without a tool result."
        ),
        "prefer_tools": True,
        "suggest_tools": [
            "list_dir",
            "file_read",
            "repo_search",
            "memory_search",
            "file_write",
            "file_edit",
            "bash_exec",
            "mission_start",
            "mission_verify",
            "job_run",
            "spread_run",
        ],
        "change_safety": True,
    },
    "build": {
        "id": "build",
        "system": (
            "[Build] Implement with file_write / file_edit. Verify after the product "
            "work exists. Don't stop to report after one hop. Don't claim done "
            "without a tool result."
        ),
        "prefer_tools": True,
        "suggest_tools": [
            "file_edit",
            "file_write",
            "repo_search",
            "file_read",
            "list_dir",
            "bash_exec",
            "job_run",
            "spread_run",
            "subgoal_open",
            "mission_start",
            "mission_verify",
            "build_status",
            "build_unit_hop",
            "build_live_project",
            "build_mutation_score",
            "build_mutant_score",
            "build_compile_spec",
            "build_tdd",
            "build_gate_tower",
            "build_repair_queue",
            "build_snapshot",
            "build_symbol_index",
            "build_resume",
            "soul_recall",
        ],
        "change_safety": True,
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
    "review": {
        "id": "review",
        "system": (
            "[Read-only] Scout once, deliver findings, stop. No file_write unless "
            "they asked to change something."
        ),
        "prefer_tools": True,
        "suggest_tools": [
            "list_dir",
            "file_read",
            "repo_search",
            "file_glob",
            "memory_search",
        ],
    },
    "tool": {
        "id": "tool",
        "system": (
            "[Work] Tools until the goal is done. Batch independent reads. "
            "file_edit existing files, file_write new ones. Don't invent a skill "
            "that is not installed. Run until finished."
        ),
        "prefer_tools": True,
        "suggest_tools": [
            "skill_activate",
            "file_edit",
            "repo_search",
            "file_read",
            "bash_exec",
            "job_run",
            "spread_run",
        ],
        "change_safety": True,
    },
    "autonomous": {
        "id": "autonomous",
        "system": (
            "[Work alone] The owner stepped away. Finish the goal with tools. "
            "mission_start a checklist with verify_command; mark steps as you go; "
            "mission_verify before claiming done. Pick reasonable defaults. "
            "Escalate only for secrets, paid APIs, or irreversible destroy."
        ),
        "prefer_tools": True,
        "suggest_tools": [
            "skill_activate",
            "mission_start",
            "mission_update",
            "mission_verify",
            "build_drive",
            "file_edit",
            "repo_search",
            "job_run",
            "spread_run",
            "bash_exec",
        ],
        "change_safety": True,
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
    # Read-only review / analysis / explain — deliver findings, do NOT get pulled
    # into the write-and-verify build loop. Wins over the generic task pack no
    # matter what the router labeled it (this is the review-loop fix).
    if looks_like_readonly_request(ut):
        return dict(_PACKS["review"])
    if key == "chat":
        if any(w in ut for w in ("remember", "what do you know", "/memory", "/whoami", "/forget")):
            return dict(_PACKS["memory"])
        if any(w in ut for w in ("skill", "/skills", "procedure")):
            return dict(_PACKS["skill"])
        if any(w in ut for w in ("plan only", "just plan", "roadmap only", "don't implement", "do not implement")):
            return dict(_PACKS["plan"])
        if any(w in ut for w in ("plan", "roadmap", "break down")) and not any(
            w in ut for w in ("build", "implement", "create", "fix", "write")
        ):
            return dict(_PACKS["plan"])
        if any(
            w in ut
            for w in (
                "implement",
                "build",
                "ship",
                "scaffold",
                "create app",
                "create a",
                "write a",
                "make me",
                "develop",
                "calculator",
            )
        ):
            return dict(_PACKS["build"])
        if any(
            w in ut
            for w in (
                "fix",
                "debug",
                "run ",
                "create file",
                "edit ",
                "refactor",
                "review",
                "investigate",
                "research",
                "set up",
                "setup",
                "add ",
                "update ",
                "change ",
            )
        ):
            # Generic work request → full task loop (research → plan → build)
            return dict(_PACKS["task"])
        if any(w in ut for w in ("soul", "who are you", "what do you remember feeling")):
            return dict(_PACKS["memory"])
        # Non-trivial request with verbs/paths → task loop, not empty chat
        if len(ut) > 40 and any(
            w in ut
            for w in (
                "please",
                "need",
                "want",
                "can you",
                "could you",
                "should",
                ".py",
                ".ts",
                ".js",
                "project",
                "file",
                "folder",
            )
        ):
            return dict(_PACKS["task"])
    if key in ("tool", "build", "task") and any(
        p in ut for p in ("alone", "on your own", "without me", "end-to-end", "end to end")
    ):
        return dict(_PACKS["autonomous"])
    # Router said "tool" without more specific pack — still use RPB task framing
    if key == "tool":
        return dict(_PACKS["task"])
    return pack


def format_policy_block(pack: dict[str, Any] | None) -> str:
    if not pack:
        return ""
    text = str(pack.get("system") or "").strip()
    # Standing change-safety for coding/autonomous turns (full checklist via skill).
    if pack.get("change_safety"):
        try:
            from remedy.core.change_safety import change_safety_block

            extra = change_safety_block()
            if extra:
                text = f"{text}\n{extra}".strip() if text else extra
        except Exception:
            pass
    tools = pack.get("suggest_tools") or []
    # Work packs already have the full tool catalog on the request. Dumping
    # 15 names here makes frontier models recap the catalog in thinking.
    # Memory / skill packs still need the hint (those tools are easy to miss).
    if tools and text and str(pack.get("id") or "") not in {
        "task",
        "build",
        "review",
        "autonomous",
    }:
        text += " Suggested tools when needed: " + ", ".join(str(t) for t in tools) + "."
    return text
