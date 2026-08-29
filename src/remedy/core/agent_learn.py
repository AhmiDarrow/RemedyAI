"""Post-turn auto-learn helpers extracted from BasicRuntime.

Keeps the personal-partner learning loop easy to unit-test without pulling
the full ReAct agent. Behavior matches the previous inline implementation.
"""

from __future__ import annotations

import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

# Meta tools alone do not count toward a "real work" multi-tool turn.
_META_TOOLS = frozenset(
    {"skill_search", "skill_activate", "skill_reload", "local_discover"}
)


_TOOL_VERB = {
    "file_write": "write",
    "file_edit": "edit",
    "file_edit_batch": "edit",
    "apply_patch": "edit",
    "bash_exec": "shell",
    "host_run": "host",
    "job_run": "verify",
    "repo_search": "search",
    "file_read": "read",
    "list_dir": "browse",
    "computer_click": "click",
    "computer_type": "type",
}

_SECRETISH = re.compile(
    r"(?i)['\"]?(api[_-]?key|secret|token|password|passwd|bearer|authorization)['\"]?"
    r"\s*[:=]\s*['\"]?\S+"
)
_AUTH_HEADER = re.compile(
    r"(?i)(?:authorization\s*[:=]\s*)?bearer\s+\S+"
)
_VAULT_TOKEN = re.compile(r"\{\{\s*vault:[^}]+\}\}")
_OMITTED = "Owner task (secrets omitted)."


def _sanitize_title_line(message: str) -> str:
    line = (message or "").strip().split("\n")[0]
    line = re.sub(r"[A-Za-z]:\\[^\s]+", "", line)
    line = re.sub(r"/[^\s]+", "", line)
    line = _VAULT_TOKEN.sub("", line)
    line = _AUTH_HEADER.sub("", line)
    line = _SECRETISH.sub("", line)
    line = re.sub(r"\s+", " ", line).strip(" -_.,")
    try:
        from remedy.memory.partner_memory import looks_like_secret

        if looks_like_secret(line):
            return ""
    except Exception:
        return ""
    return line[:60]


def safe_learn_description(message: str) -> str:
    """User text for a learned skill — never a vault token or key blob."""
    text = _VAULT_TOKEN.sub("{{vault}}", message or "")
    text = _AUTH_HEADER.sub("Bearer [redacted]", text)
    text = _SECRETISH.sub(r"\1=[redacted]", text)
    probe = re.sub(
        r"(?i)(?:api[_-]?key|secret|token|password|passwd|bearer|authorization)"
        r"=\[redacted\]",
        "",
        text,
    )
    probe = probe.replace("Bearer [redacted]", "").replace("{{vault}}", "")
    try:
        from remedy.memory.partner_memory import looks_like_secret

        if looks_like_secret(probe):
            return _OMITTED
    except Exception:
        return _OMITTED
    return text[:400]


def _skill_title_from_steps(message: str, steps: list[dict[str, Any]]) -> str:
    """Owner-facing title. Never a raw tool-name chain (those became catalog junk)."""
    line = _sanitize_title_line(message)
    if len(re.sub(r"[^A-Za-z0-9]", "", line)) >= 4:
        return line
    verbs: list[str] = []
    for s in steps:
        name = str(s.get("tool") or s.get("name") or "").strip()
        if name in _META_TOOLS or not name:
            continue
        verb = _TOOL_VERB.get(name, "")
        if verb and verb not in verbs:
            verbs.append(verb)
        if len(verbs) >= 2:
            break
    if verbs:
        return "-".join(verbs)[:60]
    return "session-task"


def _step_tool_name(step: dict[str, Any]) -> str:
    return str(step.get("tool") or step.get("name") or step.get("tool_name") or "").strip()


def _count_real_steps(
    steps: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], int]:
    """Return (non-meta tool steps, number of successful steps overall)."""
    real = [s for s in steps if _step_tool_name(s) not in _META_TOOLS]
    successes = sum(1 for s in steps if s.get("success"))
    return real, successes


def should_auto_learn_from_steps(steps: list[dict[str, Any]] | None) -> bool:
    """Return True when a turn has enough successful tool work to codify.

    Requires multi-tool diversity so trivial file_read spam does not flood
    the skill catalog (coding long-task product bug).
    """
    steps = list(steps or [])
    if len(steps) < 3:
        return False
    real, successes = _count_real_steps(steps)
    # The inner `if len(steps) < 4` this replaced was already implied by the
    # line above it, so the nesting read as an extra condition that was not one.
    if len(real) < 3 and len(steps) < 4:
        return False
    if successes < 3:
        return False
    if successes < max(2, int(0.5 * len(steps))):
        return False
    # Distinct non-meta tools: pure explore loops (read/read/list) are noise.
    names: list[str] = []
    for s in real:
        n = str(s.get("tool") or s.get("name") or s.get("tool_name") or "").strip()
        if n and n not in names:
            names.append(n)
    if len(names) < 2:
        return False
    # Need at least 4 real steps OR 3 distinct tools — hard-won short paths OK via lifecycle
    return not (len(real) < 4 and len(names) < 3)


def auto_learn_from_turn(
    *,
    learning_loop: Any,
    message: str,
    session_id: str | None,
    steps: list[dict[str, Any]] | None,
    allow_creation: bool = True,
) -> Any | None:
    """If eligible, distill tool steps into a probation skill. Returns skill or None.

    ``allow_creation=False`` (Settings → "allow skill creation" off) skips
    creating *new* skills only; evaluation of already-learned skills keeps
    running through :func:`record_skill_turn_outcome`.
    """
    if learning_loop is None:
        return None
    if not allow_creation:
        logger.debug("Auto-learn skipped: skill creation disabled in settings")
        return None
    steps_list = list(steps or [])
    if not should_auto_learn_from_steps(steps_list):
        return None

    # Pattern nanobot pre-gate: skip learning noisy / rejectable traces.
    title = _skill_title_from_steps(message, steps_list)
    try:
        from remedy.nanoswarm import get_swarm
        from remedy.nanoswarm.events import SwarmEvent

        gate = get_swarm().dispatch(
            SwarmEvent.session_end(session_id, success=True, title=title or ""),
        )
        preg = (gate.get("signals") or {}).get("pattern_pregate") or {}
        if preg.get("skip_learn") or preg.get("action") in ("reject", "skip"):
            logger.info(
                "Auto-learn skipped by pattern pregate: %s",
                preg.get("reasoning") or preg.get("action"),
            )
            return None
    except Exception:
        logger.debug("pattern pregate failed", exc_info=True)

    skill = learning_loop.learn_from_tool_steps(
        title=title or "multi-tool-task",
        steps=steps_list,
        session_id=session_id,
        description=safe_learn_description(message),
        overall_success=True,
    )
    if skill is not None:
        logger.info(
            "Auto-learned skill '%s' status=%s",
            skill.manifest.name,
            skill.manifest.status.value,
        )
    return skill


# -- closed-loop evaluation --------------------------------------------------

OUTCOME_MIN_REAL_STEPS = 3
OUTCOME_MIN_SUCCESS_RATIO = 0.5


def _is_auto_generated(name: str, registry: Any) -> bool:
    """True when the registry knows ``name`` as an auto-learned skill."""
    if registry is None:
        return False
    skill = None
    try:
        getter = getattr(registry, "get_skill", None) or getattr(registry, "get", None)
        if callable(getter):
            skill = getter(name)
    except Exception:
        skill = None
    if skill is None:
        try:
            for sk in list(getattr(registry, "skills", []) or []):
                if getattr(getattr(sk, "manifest", None), "name", None) == name:
                    skill = sk
                    break
        except Exception:
            skill = None
    if skill is None:
        return False
    meta = getattr(getattr(skill, "manifest", None), "metadata", None) or {}
    return bool(meta.get("auto_generated"))


def record_skill_turn_outcome(
    learning_loop: Any,
    *,
    skills: list[str],
    steps: list[dict[str, Any]] | None,
    aborted: bool,
    session_id: str,
    registry: Any = None,
) -> dict[str, bool]:
    """Grade the skills used this turn by how the turn went.

    success  := not aborted and >= 3 successful steps and ratio >= 0.5
    failure  := aborted, or >= 3 real steps with ratio < 0.5
    otherwise nothing is recorded (short turns are not evidence).

    Only auto-generated skills are graded — curated bundled skills are not
    on probation. Returns ``{skill_name: ok}`` for what was recorded.
    """
    if learning_loop is None:
        return {}
    names: list[str] = []
    for n in skills or []:
        n = str(n or "").strip()
        if n and n not in names:
            names.append(n)
    if not names:
        return {}
    steps_list = list(steps or [])
    real, successes = _count_real_steps(steps_list)
    ratio = (successes / len(steps_list)) if steps_list else 0.0
    if aborted:
        ok: bool | None = False
    elif successes >= OUTCOME_MIN_REAL_STEPS and ratio >= OUTCOME_MIN_SUCCESS_RATIO:
        ok = True
    elif len(real) >= OUTCOME_MIN_REAL_STEPS and ratio < OUTCOME_MIN_SUCCESS_RATIO:
        ok = False
    else:
        ok = None
    if ok is None:
        return {}
    refiner = getattr(learning_loop, "refiner", None)
    record = getattr(refiner, "record_execution", None)
    if not callable(record):
        record = getattr(learning_loop, "record_skill_feedback", None)
    if not callable(record):
        return {}
    reg = registry if registry is not None else getattr(learning_loop, "registry", None)
    out: dict[str, bool] = {}
    for name in names:
        if not _is_auto_generated(name, reg):
            continue
        try:
            record(
                name,
                ok,
                0.0,
                str(session_id or ""),
                "turn aborted" if aborted and not ok else None,
            )
        except Exception:
            logger.debug("record_skill_turn_outcome failed for %s", name, exc_info=True)
            continue
        out[name] = ok
    if out:
        logger.debug("Skill turn outcome recorded: %s", out)
    return out
