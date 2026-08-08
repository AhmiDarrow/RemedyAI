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
# False completion prose without tool proof (builder loop)
_CLAIM_SHIPPED = re.compile(
    r"(?i)\b("
    r"(?:all )?(?:done|finished|complete|shipped|implemented|fixed)|"
    r"ready to (?:merge|ship|deploy)|"
    r"should work now|you(?:'re| are) good to go|"
    r"that(?:'s| is) (?:it|everything)"
    r")\b"
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
    from remedy.core.metabolism.redact import looks_like_secret_text

    t = text or ""
    kinds: list[str] = []
    if _TESTS_GREEN.search(t) or _TESTS_FAIL.search(t):
        kinds.append("tests")
    if _PLAN_DONE.search(t):
        kinds.append("plan_done")
    if looks_like_secret_text(t):
        kinds.append("secret_risk")
    return kinds


def verify_critical(
    *,
    assistant_text: str = "",
    recent_tool_texts: list[str] | None = None,
    triggers: list[str] | None = None,
) -> VerifyResult:
    """Heuristic critical verify — no network, one voice remedies."""
    from remedy.core.metabolism.redact import looks_like_secret_text

    tools = "\n".join(recent_tool_texts or [])
    blob = f"{assistant_text}\n{tools}"
    kinds = list(triggers or []) or should_verify_text(blob)

    if "secret_risk" in kinds or looks_like_secret_text(blob):
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

    # Shipped/done claims with no tool activity in this turn → push verify
    asst = assistant_text or ""
    if (
        _CLAIM_SHIPPED.search(asst)
        and len(asst) < 1200
        and not (recent_tool_texts or [])
        and "tests" not in kinds
    ):
        return VerifyResult(
            ok=False,
            kind="done_without_tools",
            message="Done/shipped claim without tool evidence this turn",
            silent_remedy=(
                "[Verify · builder] You claimed done without running tools. "
                "If work remains, use file_read/file_edit/bash_exec and verify "
                "(tests or mission_verify). Do not restate completion."
            ),
        )

    if not kinds:
        return VerifyResult(ok=True, kind="none", message="no_triggers")

    return VerifyResult(ok=True, kind=",".join(kinds), message="pass")
