"""Helper nanobot — offline local assist (same Qwen role reserved for later neural).

Ships deterministic offline drafts now: slash/help FAQ, explain last error,
and continuity tips. Does not start llama-server; never a second chat voice.
"""

from __future__ import annotations

from typing import Any

_HELP_CARDS: list[dict[str, str]] = [
    {
        "id": "switch-provider",
        "title": "Switch provider mid-session",
        "body": (
            "Use the status-bar Provider and Model pickers. Switching remeasures "
            "context fill under NanoToken for the new model. It does not wipe "
            "usage history. Stop generation before switching."
        ),
    },
    {
        "id": "usage",
        "title": "Usage & cost",
        "body": (
            "Click Usage on the status bar for multiprovider tokens, cost by "
            "provider/model, and harness continuity metrics."
        ),
    },
    {
        "id": "approvals",
        "title": "Approvals",
        "body": (
            "Default Ask mode prompts for shell, file write, and skill scripts. "
            "Status-bar approval mode Auto skips routine prompts — use only in "
            "trusted workspaces. /approve and /deny work when an id is shown."
        ),
    },
    {
        "id": "harness",
        "title": "Memory harness",
        "body": (
            "Harness Auto compresses tool sludge when context fill is high. "
            "Use /compact to force, /harness for status. Continuity workers "
            "(Token, Pattern, Pack, Guard) stay silent in chat."
        ),
    },
    {
        "id": "nanotoken-bpe",
        "title": "NanoToken BPE (owned)",
        "body": (
            "Token counts use Remedy's own byte-level BPE pack when present "
            "(default remedy-bbpe-v2; v1 kept for fallback). Assigned per provider "
            "by the continuity swarm. Provider API usage is still billing truth. "
            "Retrain via scripts/nanotoken_battery_and_train.py or "
            "train_nanotoken_bpe.py — no third-party tokenizers are shipped. "
            "Set REMEDY_BPE=0 to force heuristics."
        ),
    },
    {
        "id": "skills-scale",
        "title": "Many skills (100+)",
        "body": (
            "Hot catalog is budgeted (default 80). Archive unused skills in the "
            "Skills panel so ranking stays sharp. Export packs for offline backup."
        ),
    },
    {
        "id": "security",
        "title": "Local-first security",
        "body": (
            "API binds to 127.0.0.1 with Bearer auth by default. Imported skill "
            "packs start quarantined until you Trust them. F1 → Security & data."
        ),
    },
]

_ERROR_HINTS: list[tuple[str, str]] = [
    ("401", "Unauthorized — check provider API key / OAuth in Settings, or local Bearer token."),
    ("403", "Forbidden — scope or auth blocked the action. Narrow the path or Trust the skill."),
    ("404", "Not found — list_dir the parent, or refresh models if the model id was renamed."),
    ("429", "Rate limited — wait, switch provider, or use a cheaper model."),
    ("timeout", "Timed out — retry with a narrower tool call or check local Ollama/vision."),
    ("connection", "Network/connection — is the provider up? Ollama running? Sidecar healthy?"),
    ("approval_required", "Needs your approval in the banner (or /approve <id>)."),
    ("security_block", "Blocked by security policy — use safer tools (file_read) or rephrase."),
    ("zip slip", "Import rejected unsafe zip paths — use a clean skill pack."),
    ("empty command", "Shell got an empty command — pass a real argv string."),
]


class HelperNanobot:
    """Offline help / error explain. Optional local model later (role=helper)."""

    def __init__(self) -> None:
        self.enabled = True  # offline surface on; neural assist still off
        self.jobs_run = 0
        self.neural_enabled = False  # reserved — shared Qwen when productized

    def status(self) -> dict[str, Any]:
        return {
            "bot": "helper",
            "enabled": self.enabled,
            "neural_enabled": self.neural_enabled,
            "jobs_run": self.jobs_run,
            "role_model": "qwen2.5-vl-3b",
            "note": "Offline FAQ/error drafts; neural helper reserved.",
        }

    def draft_help(self, prompt: str = "") -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "Helper bot disabled"}
        self.jobs_run += 1
        q = (prompt or "").strip().lower()
        cards = list(_HELP_CARDS)
        if q:
            scored: list[tuple[int, dict[str, str]]] = []
            for c in cards:
                blob = (c["id"] + " " + c["title"] + " " + c["body"]).lower()
                score = sum(1 for w in q.split() if w and w in blob)
                if score:
                    scored.append((score, c))
            scored.sort(key=lambda x: -x[0])
            cards = [c for _, c in scored[:4]] or cards[:3]
        else:
            cards = cards[:4]
        lines = [f"**{c['title']}**\n{c['body']}" for c in cards]
        return {
            "ok": True,
            "method": "offline",
            "cards": cards,
            "markdown": "\n\n".join(lines),
            "prompt": (prompt or "")[:200],
        }

    def explain_error(self, error_text: str = "") -> dict[str, Any]:
        if not self.enabled:
            return {"ok": False, "error": "Helper bot disabled"}
        self.jobs_run += 1
        text = (error_text or "").strip()
        low = text.lower()
        hits: list[str] = []
        for needle, hint in _ERROR_HINTS:
            if needle in low:
                hits.append(hint)
        if not hits:
            hits.append(
                "No specific playbook matched. Check stderr, retry with narrower "
                "scope, or open F1 → Troubleshooting."
            )
        return {
            "ok": True,
            "method": "offline",
            "hints": hits[:5],
            "markdown": "**What this might mean**\n\n" + "\n".join(f"- {h}" for h in hits[:5]),
            "error_preview": text[:400],
        }

    def continuity_tip(self, *, fill_pct: float = 0.0, stuck_rate: float = 0.0) -> str:
        if fill_pct >= 0.9:
            return "Context is very full — consider /compact or a fresh session for heavy tools."
        if fill_pct >= 0.75:
            return "Context filling up — harness may soft-nudge compress soon."
        if stuck_rate >= 0.3:
            return "Stuck signals elevated — try a different tool or restate the goal."
        return ""
