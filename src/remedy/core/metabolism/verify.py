"""Silent critical-claim verify at judgment points only.

Never multi-model debate UI. Heuristic-first; optional second model later.
L0/L1 never verify. Rate-limited by caller/governor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

_TESTS_GREEN = re.compile(
    r"(?i)\b("
    r"all tests? pass(ed)?|tests? (are )?green|pytest.*passed|"
    r"\d+ passed\b(?!.*\bfailed\b)|build succeeded|ci (is )?green"
    r")\b"
)
_TESTS_FAIL = re.compile(
    r"(?i)\b("
    r"\d+ failed|ERROR|FAILED|tests? fail|build failed|traceback"
    r")\b"
)
_PLAN_DONE = re.compile(
    r"(?i)\b("
    r"plan (is )?(done|complete|finished)|all steps? (done|complete)|"
    r"mission (complete|done|verified)"
    r")\b"
)
_SECRET_LEAK = re.compile(
    r"(?i)(api[_-]?key\s*[:=]\s*\S|password\s*[:=]\s*\S|"
    r"sk-[a-z0-9]{10,}|xai-[a-z0-9]{10,}|bearer\s+[a-z0-9._-]{16,})"
)


@dataclass(frozen=True)
class VerifyResult:
    ok: bool
    kind: str
    message: str
    silent_remedy: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "kind": self.kind,
            "message": self.message,
            "has_remedy": bool(self.silent_remedy),
        }


def should_verify_text(text: str) -> list[str]:
    """Return trigger kinds present in assistant or tool text."""
    t = text or ""
    kinds: list[str] = []
    if _TESTS_GREEN.search(t) or _TESTS_FAIL.search(t):
        kinds.append("tests")
    if _PLAN_DONE.search(t):
        kinds.append("plan_done")
    if _SECRET_LEAK.search(t):
        kinds.append("secret_risk")
    return kinds


def verify_critical(
    *,
    assistant_text: str = "",
    recent_tool_texts: list[str] | None = None,
    triggers: list[str] | None = None,
) -> VerifyResult:
    """Heuristic critical verify — no network, one voice remedies."""
    tools = "\n".join(recent_tool_texts or [])
    blob = f"{assistant_text}\n{tools}"
    kinds = list(triggers or []) or should_verify_text(blob)

    if "secret_risk" in kinds or _SECRET_LEAK.search(blob):
        return VerifyResult(
            ok=False,
            kind="secret_risk",
            message="Potential secret material in outbound content",
            silent_remedy=(
                "[Verify] Do not echo secrets/API keys. Redact and continue safely."
            ),
        )

    if "tests" in kinds:
        if _TESTS_FAIL.search(tools) and _TESTS_GREEN.search(assistant_text or ""):
            return VerifyResult(
                ok=False,
                kind="tests_false_green",
                message="Assistant claimed tests green but tool output shows failures",
                silent_remedy=(
                    "[Verify] Tool output still shows failures — do not claim tests passed; fix and re-run."
                ),
            )
        if _TESTS_GREEN.search(tools) and not _TESTS_FAIL.search(tools):
            return VerifyResult(ok=True, kind="tests_ok", message="tests look green")

    if "plan_done" in kinds:
        # Without mission tool proof, soft caution
        if "mission_verify" not in tools.lower() and "passed" not in tools.lower():
            return VerifyResult(
                ok=False,
                kind="plan_done_unverified",
                message="Done claim without verify evidence",
                silent_remedy=(
                    "[Verify] Before claiming done, run mission_verify or tests and confirm evidence."
                ),
            )

    if not kinds:
        return VerifyResult(ok=True, kind="none", message="no_triggers")

    return VerifyResult(ok=True, kind=",".join(kinds), message="pass")
