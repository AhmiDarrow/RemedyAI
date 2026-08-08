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
            "[Default task loop] RESEARCH → PLAN → BUILD until done.\n"
            "1) RESEARCH: batch list_dir / file_read / repo_search / memory_search "
            "(and web when needed) in one step — gather ground truth.\n"
            "2) PLAN: set brief open_tasks or mission_start checklist (short, concrete).\n"
            "3) BUILD: file_write / file_edit, then verify (bash_exec / mission_verify); "
            "repair until green. Never monologue a plan and stop. Never claim done "
            "without a tool result or verify signal."
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
            "[Continuity · Build engine] User wants software built or shipped. "
            "Default loop: RESEARCH → PLAN → BUILD.\n"
            "RESEARCH = SCOUT (parallel file_read/list_dir/repo_search in ONE step). "
            "PLAN = short checklist (brief/mission). "
            "BUILD = IMPLEMENT (file_write / multi-hunk file_edit) → VERIFY "
            "(bash_exec tests / job_run kind=verify / mission_verify) → REPAIR until "
            "green → DONE. Never monologue a plan without tool_calls. Never claim "
            "done without verify. spread_run for multi-module surveys; mission_start "
            "for unattended end-to-end; subgoal_open for phases. Soul keeps identity."
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
    "tool": {
        "id": "tool",
        "system": (
            "[Continuity] User wants work done on the machine or project. "
            "Default: RESEARCH → PLAN → BUILD.\n"
            "RESEARCH with tools first (repo_search, file_read, list_dir — batch "
            "independent calls in ONE step). PLAN briefly (open tasks / mission). "
            "BUILD with file_edit (multi-hunk via edits=) / file_write / bash_exec "
            "(timeout_seconds/workdir). "
            "When ≥2 independent modules/paths, use spread_run. "
            "Prefer installed skill_activate; if [Library] pack is not installed — "
            "tell the user, do not invent its body. Run until finished."
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
            "[Continuity · Work alone] The user stepped away or asked you to handle "
            "this end-to-end without check-ins. Act with high agency:\n"
            "1) mission_start with goal, checklist steps, and verify_command "
            "(stack fingerprint may auto-fill; e.g. pytest -q or Godot headless smoke).\n"
            "2) Prefer file_edit / multi-hunk edits=; repo_search for discovery; "
            "file_write only for new files. Use absolute paths when juggling trees. "
            "Batch many independent reads in one step — serial one-file-per-step is too slow.\n"
            "3) Cover ground fast: spread_run when ≥2 independent trees/modules; "
            "else job_run kind=explore|verify|diff for a single survey.\n"
            "4) mission_update after each step; mission_verify before claiming done "
            "(do not claim complete if verify failed).\n"
            "5) On failure: read errors (path:line), file_edit fixes, re-verify "
            "(raise timeout_seconds for long builds).\n"
            "6) Run until finished — same class of agency as a long Build session. "
            "Soft epochs only compact context; never stop for a step/tool budget.\n"
            "Do not stop at a question when you can pick a reasonable default. "
            "Escalate only for secrets, paid APIs, or irreversible destroy. "
            "Summarize only when the work is actually done."
        ),
        "prefer_tools": True,
        "suggest_tools": [
            "skill_activate",
            "mission_start",
            "mission_update",
            "mission_verify",
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
    if tools and text:
        text += " Suggested tools when needed: " + ", ".join(str(t) for t in tools) + "."
    return text
