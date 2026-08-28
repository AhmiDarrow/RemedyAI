"""High-confidence personal-assistant fast path — no provider tokens.

Matches obvious tool-only requests (calendar list, budget/debt/bills status,
brief, simple expense log). Unmatched or low-confidence → None (provider loop).

Does not replace multi-step reasoning, computer-use, or ambiguous asks.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from remedy.core.build_oracle import coerce_text_arg


@dataclass(frozen=True)
class FastPathPlan:
    """One tool call that can answer the user without an LLM turn."""

    tool: str
    arguments: dict[str, Any] = field(default_factory=dict)
    label: str = ""  # short log label


# ── matchers (order matters: more specific first) ───────────────────────────

_BRIEF_RE = re.compile(
    r"(?is)^\s*("
    r"(give me |show me |get |run |do )?(my |a |the )?(daily |morning )?brief"
    r"|assistant[_\s-]?brief"
    r"|what('?s| is) (on )?(my )?plate( today)?"
    r")\s*[.?!]?\s*$"
)

_CAL_LIST_RE = re.compile(
    r"(?is)^\s*("
    r"(what('?s| is)|show|list|check|get|see|view)\s+"
    r"((on |in )?(my )?)?(calendar|schedule|agenda|events?)"
    r"( for)?( (this|the|next|upcoming))?( week| day| today| tomorrow| \d+ days?)?"
    r"|(my )?(calendar|schedule|agenda)( (this|next)? ?week| today| tomorrow)?"
    r"|upcoming events?"
    r"|what do i have (on|scheduled|coming up)"
    r")\s*[.?!]?\s*$"
)

_BUDGET_STATUS_RE = re.compile(
    r"(?is)^\s*("
    r"(show |get |check |what('?s| is) )?(my )?(budget|budget status|spending)"
    r"( status| summary| remaining)?"
    r"|how much (have i|did i) spend(t)?"
    r"|budget_get|budget_status"
    r")\s*[.?!]?\s*$"
)

_DEBT_LIST_RE = re.compile(
    r"(?is)^\s*("
    r"(list |show |get )?(my )?(debts?|debt list)"
    r"|what debts? do i have"
    r"|debt_list"
    r")\s*[.?!]?\s*$"
)

_BILL_LIST_RE = re.compile(
    r"(?is)^\s*("
    r"(list |show |get )?(my )?(bills?|bill list)"
    r"|what bills? do i have"
    r"|bill_list"
    r")\s*[.?!]?\s*$"
)

_ACCOUNTS_RE = re.compile(
    r"(?is)^\s*("
    r"(list |show |get )?(my )?(linked )?accounts?"
    r"|assistant accounts?"
    r"|is google connected"
    r"|assistant_accounts"
    r")\s*[.?!]?\s*$"
)

_MAIL_LIST_RE = re.compile(
    r"(?is)^\s*("
    r"(check |show |list |get |open )?(my )?(email|emails|inbox|gmail|mail)"
    r"( messages?)?"
    r"|any (new )?(mail|email|emails)\??"
    r"|what('?s| is) (in )?(my )?inbox"
    r"|mail_list"
    r")\s*[.?!]?\s*$"
)

# log $50 to groceries / spent 12.5 on coffee
_TX_RE = re.compile(
    r"(?is)^\s*("
    r"(log|record|add|track)\s+"
    r"\$?\s*(?P<amt>\d+(?:[.,]\d{1,2})?)\s*"
    r"(dollars?\s+)?"
    r"(to|on|for|in)?\s*"
    r"(?P<cat>[a-z][a-z0-9 _-]{0,40})"
    r"|spent\s+\$?\s*(?P<amt2>\d+(?:[.,]\d{1,2})?)\s+"
    r"(on|for)\s+(?P<cat2>[a-z][a-z0-9 _-]{0,40})"
    r")\s*[.?!]?\s*$"
)

_DISCLAIMER_RE = re.compile(
    r"(?is)^\s*(money|budget|debt)\s+(disclaimer|advice warning)\s*[.?!]?\s*$"
)

# Reject multi-clause / reasoning asks even if a keyword matches
_COMPLEX_RE = re.compile(
    r"(?is)\b("
    r"and then|after that|also |plus |but |should i|would you|"
    r"plan |help me (decide|figure)|compare |why |how (do|can|should) i|"
    r"write |email |draft |refactor|implement|debug|code "
    r")\b"
)


def match_assistant_fast_path(message: str) -> FastPathPlan | None:
    """Return a plan if *message* is a high-confidence PA-only request."""
    msg = coerce_text_arg(message)
    if not msg or len(msg) > 160:
        return None
    if _COMPLEX_RE.search(msg):
        return None
    # Multi-sentence → let the provider model handle
    if msg.count(".") + msg.count("?") + msg.count("!") > 1:
        return None
    if "\n" in msg:
        return None

    if _BRIEF_RE.match(msg):
        return FastPathPlan("assistant_brief", {}, "brief")
    if _CAL_LIST_RE.match(msg):
        days = 7
        low = msg.lower()
        if "today" in low:
            days = 1
        elif "tomorrow" in low:
            days = 2
        elif re.search(r"\b(\d+)\s*days?\b", low):
            days = max(1, min(31, int(re.search(r"\b(\d+)\s*days?\b", low).group(1))))  # type: ignore[union-attr]
        return FastPathPlan("calendar_list_events", {"days": days}, "calendar_list")
    if _BUDGET_STATUS_RE.match(msg):
        return FastPathPlan("budget_status", {}, "budget_status")
    if _DEBT_LIST_RE.match(msg):
        return FastPathPlan("debt_list", {}, "debt_list")
    if _BILL_LIST_RE.match(msg):
        return FastPathPlan("bill_list", {}, "bill_list")
    if _ACCOUNTS_RE.match(msg):
        return FastPathPlan("assistant_accounts", {}, "accounts")
    if _MAIL_LIST_RE.match(msg):
        return FastPathPlan("mail_list", {"query": "in:inbox", "limit": 12}, "mail_list")
    if _DISCLAIMER_RE.match(msg):
        return FastPathPlan("money_disclaimer", {}, "disclaimer")

    tm = _TX_RE.match(msg)
    if tm:
        amt_s = tm.group("amt") or tm.group("amt2") or "0"
        cat = (tm.group("cat") or tm.group("cat2") or "misc").strip(" .,-")
        try:
            amount = float(amt_s.replace(",", "."))
        except ValueError:
            return None
        if amount <= 0 or amount > 1_000_000:
            return None
        if not cat or len(cat) > 40:
            return None
        return FastPathPlan(
            "budget_tx_add",
            {"amount": amount, "category": cat, "kind": "expense"},
            "budget_tx",
        )

    return None


def format_fast_path_reply(tool: str, raw: str) -> str:
    """Turn tool JSON/text into a short user-facing reply (no LLM)."""
    text = (raw or "").strip()
    if not text:
        return "Done."

    if tool == "money_disclaimer":
        return text

    if tool == "assistant_brief":
        # Already markdown from the tool
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

    if tool == "calendar_list_events":
        events = data.get("events") or []
        if not events:
            return str(data.get("message") or "No upcoming events.")
        lines = [f"**Calendar** ({data.get('count', len(events))}):"]
        for e in events[:20]:
            if not isinstance(e, dict):
                continue
            lines.append(f"- {e.get('start', '?')}: {e.get('title', '(no title)')}")
        return "\n".join(lines)

    if tool == "budget_status":
        if data.get("message") and not data.get("categories"):
            return str(data["message"])
        lines = [f"**Budget** {data.get('label') or ''}".strip()]
        for row in (data.get("categories") or [])[:20]:
            if not isinstance(row, dict):
                continue
            lines.append(
                f"- {row.get('category')}: spent {row.get('spent')} / "
                f"planned {row.get('planned')} (left {row.get('remaining')})"
            )
        if data.get("disclaimer"):
            lines.append(f"_{data['disclaimer']}_")
        return "\n".join(lines) if len(lines) > 1 else str(data.get("message") or "No budget.")

    if tool == "budget_tx_add":
        return str(data.get("message") or "Logged.")

    if tool in ("debt_list", "bill_list"):
        key = "debts" if tool == "debt_list" else "bills"
        items = data.get(key) or []
        if not items:
            return str(data.get("message") or f"No {key}.")
        lines = [f"**{key.title()}** ({len(items)}):"]
        for it in items[:25]:
            if not isinstance(it, dict):
                continue
            if tool == "debt_list":
                lines.append(
                    f"- {it.get('name')}: ${float(it.get('balance') or 0):.2f} "
                    f"@ {it.get('apr_pct')}% APR"
                )
            else:
                lines.append(
                    f"- {it.get('name')}: ${float(it.get('amount') or 0):.2f} "
                    f"{it.get('cadence') or ''} next={it.get('next_due') or '?'}"
                )
        if data.get("disclaimer"):
            lines.append(f"_{data['disclaimer']}_")
        return "\n".join(lines)

    if tool == "assistant_accounts":
        lines = ["**Personal assistant**"]
        if data.get("enabled") is False:
            lines.append("Features disabled in Settings.")
        accts = data.get("accounts") or []
        if accts:
            for a in accts:
                if isinstance(a, dict):
                    lines.append(
                        f"- {a.get('provider')}: {a.get('status')} {a.get('email') or ''}".rstrip()
                    )
        else:
            g = next(
                (
                    p
                    for p in (data.get("providers_planned") or [])
                    if isinstance(p, dict) and p.get("id") == "google"
                ),
                None,
            )
            st = (g or {}).get("status") or "not connected"
            lines.append(f"- Google (Gmail): {st}")
        lines.append(
            f"Budget: {'set' if data.get('has_budget') else 'none'} · "
            f"debts {data.get('debt_count', 0)} · bills {data.get('bill_count', 0)}"
        )
        return "\n".join(lines)

    if tool == "mail_list":
        messages = data.get("messages") or []
        if not messages:
            return str(data.get("message") or "No messages.")
        lines = [f"**Inbox** ({data.get('count', len(messages))}):"]
        for m in messages[:20]:
            if not isinstance(m, dict):
                continue
            lines.append(
                f"- {m.get('from') or '?'}: {m.get('subject') or '(no subject)'} "
                f"— {(m.get('snippet') or '')[:60]}"
            )
        return "\n".join(lines)

    # Generic
    if data.get("message"):
        return str(data["message"])
    return text[:2000]


def assistant_fast_path_enabled(home: Any = None) -> bool:
    """Respect Settings → Personal assistant enabled flag."""
    try:
        from remedy.assistant.store import get_assistant_store

        return bool(get_assistant_store(home).get_prefs().enabled)
    except Exception:
        return True
