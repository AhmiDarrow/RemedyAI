"""Away mode — the owner stepped out; the machine keeps building.

Escalate only for secrets, paid APIs, irreversible destroy, or Ask-mode
approval. No clarifying questions. Same loop as work-alone, stamped on the
build turn so green-gate / auto-drive stay aggressive.
"""

from __future__ import annotations

import re

_AWAY_RE = re.compile(
    r"(?i)\b("
    r"work alone|on your own|handle this on your own|"
    r"i need to go|be with my kids|step(ping)? away|"
    r"don'?t wait for me|do not wait for me|"
    r"unattended|fully autonomous|finish without me|"
    r"take it from here|you got this|i'?m (heading |stepping )?out|"
    r"away mode|keep going without me|do it while i'?m gone"
    r")\b"
)

AWAY_ADDENDUM = """
## Away mode (owner stepped out)

You are finishing this request without check-ins.
- Do not ask clarifying questions. Pick a reasonable default and continue.
- mission_start + build_drive / isolated hops + verify. Repair until green.
- Escalate ONLY for: secrets, paid APIs, irreversible destroy, APPROVAL_REQUIRED.
- When verify is green and todos are closed, write a short summary and stop.
- If you are blocked on approval, say what is waiting — once — then idle.
- Open life goals: take one local Life note (never send, pay, publish, delete).
- When they return they can say "I'm back" for a digest of what you already did.
""".strip()


def looks_like_away_request(message: str) -> bool:
    return bool(_AWAY_RE.search(message or ""))


def format_away_block() -> str:
    return AWAY_ADDENDUM


def away_blocker_message(kind: str, detail: str = "") -> dict[str, str]:
    return {
        "role": "user",
        "content": (
            f"[Away mode · blocker · {kind}]\n"
            f"{detail}\n"
            "Do not invent success. Wait for the owner on this one item only."
        ),
    }
