"""Register personal-assistant tools (accounts status, budget/debt/bills, brief stub)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from remedy.assistant.disclaimer import MONEY_DISCLAIMER_FULL, MONEY_DISCLAIMER_SHORT
from remedy.assistant.store import get_assistant_store


def register_assistant_tools(runtime: Any) -> None:
    """Always-on PA organization tools; calendar/mail OAuth comes later."""

    home = None
    with __import__("contextlib").suppress(Exception):
        home = getattr(getattr(runtime, "config", None), "home_dir", None)
    store = get_assistant_store(home)

    async def assistant_accounts() -> str:
        """Status of personal-assistant linked accounts and prefs (no secrets)."""
        return json.dumps(store.public_status(), indent=2)

    async def assistant_brief(hint: str = "") -> str:
        """On-demand brief from goals + local budget/bills (calendar/mail when linked)."""
        prefs = store.get_prefs()
        parts: list[str] = ["# Assistant brief", ""]
        if prefs.brief.include_calendar:
            try:
                from datetime import UTC, datetime, timedelta

                from remedy.assistant.providers.google_calendar import get_google_calendar

                cal = get_google_calendar(home)
                if cal is not None:
                    now = datetime.now(UTC)
                    events = cal.list_events(
                        time_min=now.isoformat().replace("+00:00", "Z"),
                        time_max=(now + timedelta(days=7)).isoformat().replace("+00:00", "Z"),
                    )
                    parts.append("## Calendar (next 7 days)")
                    if not events:
                        parts.append("No upcoming events on primary calendar.")
                    else:
                        for ev in events[:15]:
                            parts.append(f"- {ev.start}: {ev.title}")
                    parts.append("")
                else:
                    parts.append("## Calendar")
                    parts.append(
                        "Not connected — Settings → Personal assistant → Connect Google (Gmail)."
                    )
                    parts.append("")
            except Exception as exc:
                parts.append("## Calendar")
                parts.append(f"(Could not load calendar: {exc})")
                parts.append("")
        if prefs.brief.include_mail:
            try:
                from remedy.assistant.providers.google_gmail import get_google_gmail

                mail = get_google_gmail(home)
                if mail is not None:
                    msgs = mail.list_messages(query="in:inbox", limit=8)
                    parts.append("## Inbox (recent)")
                    if not msgs:
                        parts.append("No recent inbox messages.")
                    else:
                        for m in msgs:
                            parts.append(
                                f"- {m.from_addr or '?'}: {m.subject} — {(m.snippet or '')[:80]}"
                            )
                    parts.append("")
                else:
                    parts.append("## Mail")
                    parts.append(
                        "Not connected — Settings → Personal assistant → Connect Google (Gmail)."
                    )
                    parts.append("")
            except Exception as exc:
                parts.append("## Mail")
                parts.append(f"(Could not load mail: {exc})")
                parts.append("")
        if prefs.brief.include_budget:
            st = store.budget_status()
            parts.append("## Budget")
            parts.append(st.get("message") or "No budget")
            for row in (st.get("categories") or [])[:12]:
                parts.append(
                    f"- {row['category']}: spent {row['spent']} / planned {row['planned']} "
                    f"(remaining {row['remaining']})"
                )
            parts.append("")
        bills = store.list_bills()
        if bills:
            parts.append("## Bills")
            for b in bills[:15]:
                parts.append(
                    f"- {b.name}: ${b.amount:.2f} {b.cadence} next={b.next_due or '?'}"
                )
            parts.append("")
        debts = store.list_debts()
        if debts:
            parts.append("## Debts (user-entered)")
            for d in debts[:15]:
                parts.append(
                    f"- {d.name}: balance ${d.balance:.2f}, APR {d.apr_pct}%, min ${d.min_payment:.2f}"
                )
            parts.append("")
        # Goals from runtime if available
        with __import__("contextlib").suppress(Exception):
            tasks = list(runtime.list_tasks() or [])
            open_g = [
                t
                for t in tasks
                if "goal" in (getattr(t, "tags", None) or [])
                and str(getattr(t, "status", "")).lower()
                not in ("completed", "taskstatus.completed")
            ]
            if open_g:
                parts.append("## Open goals")
                for t in open_g[:12]:
                    parts.append(f"- {getattr(t, 'title', t)}")
                parts.append("")
        parts.append("## Linked accounts")
        accts = store.accounts_public()
        if not accts:
            parts.append(
                "None connected yet. Connect Google (Gmail) in Settings → Personal assistant. "
                "Microsoft/Yahoo next."
            )
        else:
            for a in accts:
                parts.append(f"- {a.get('provider')}: {a.get('status')} {a.get('email')}")
        parts.append("")
        parts.append(f"_Note: {MONEY_DISCLAIMER_SHORT}_")
        if hint:
            parts.append(f"\n(User hint: {hint[:200]})")
        return "\n".join(parts)

    async def calendar_list_events(days: int = 7, time_min: str = "", time_max: str = "") -> str:
        """List primary Google Calendar events (requires Connect Google)."""
        from datetime import UTC, datetime, timedelta

        from remedy.assistant.providers.google_calendar import get_google_calendar

        cal = get_google_calendar(home)
        if cal is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Google Calendar not connected. Settings → Personal assistant → Connect Google.",
                },
                indent=2,
            )
        now = datetime.now(UTC)
        tmin = (time_min or "").strip() or now.isoformat().replace("+00:00", "Z")
        if (time_max or "").strip():
            tmax = time_max.strip()
        else:
            d = max(1, min(int(days or 7), 31))
            tmax = (now + timedelta(days=d)).isoformat().replace("+00:00", "Z")
        try:
            events = cal.list_events(time_min=tmin, time_max=tmax)
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        return json.dumps(
            {
                "ok": True,
                "time_min": tmin,
                "time_max": tmax,
                "count": len(events),
                "events": [
                    {
                        "id": e.id,
                        "title": e.title,
                        "start": e.start,
                        "end": e.end,
                        "location": e.location,
                        "description": (e.description or "")[:400],
                    }
                    for e in events
                ],
                "message": f"{len(events)} event(s) on primary calendar",
            },
            indent=2,
        )

    async def calendar_create_event(
        title: str = "",
        start: str = "",
        end: str = "",
        description: str = "",
    ) -> str:
        """Create an event on primary Google Calendar (ISO start/end or YYYY-MM-DD all-day)."""
        from remedy.assistant.providers.google_calendar import get_google_calendar

        cal = get_google_calendar(home)
        if cal is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Google Calendar not connected. Settings → Personal assistant → Connect Google.",
                },
                indent=2,
            )
        if not (title or "").strip() or not (start or "").strip() or not (end or "").strip():
            return json.dumps(
                {
                    "ok": False,
                    "message": "Need title, start, and end (ISO datetime or YYYY-MM-DD for all-day).",
                },
                indent=2,
            )
        try:
            ev = cal.create_event(
                title=title.strip(),
                start=start.strip(),
                end=end.strip(),
                description=description or "",
            )
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        return json.dumps(
            {
                "ok": True,
                "event": {
                    "id": ev.id,
                    "title": ev.title,
                    "start": ev.start,
                    "end": ev.end,
                },
                "message": f"Created: {ev.title} @ {ev.start}",
            },
            indent=2,
        )

    async def mail_list(query: str = "in:inbox", limit: int = 15) -> str:
        """List Gmail messages (needs Connect Google / Gmail)."""
        from remedy.assistant.providers.google_gmail import get_google_gmail

        mail = get_google_gmail(home)
        if mail is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": (
                        "Gmail not connected. Settings → Personal assistant → "
                        "Google (Gmail) → Connect."
                    ),
                },
                indent=2,
            )
        try:
            msgs = mail.list_messages(
                query=(query or "in:inbox").strip() or "in:inbox",
                limit=int(limit or 15),
            )
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        return json.dumps(
            {
                "ok": True,
                "count": len(msgs),
                "query": query or "in:inbox",
                "messages": [
                    {
                        "id": m.id,
                        "subject": m.subject,
                        "from": m.from_addr,
                        "snippet": (m.snippet or "")[:200],
                        "date": m.date,
                        "thread_id": m.thread_id,
                    }
                    for m in msgs
                ],
                "message": f"{len(msgs)} message(s)",
            },
            indent=2,
        )

    async def mail_get(message_id: str = "") -> str:
        """Read one Gmail message by id (body snippet / plain text)."""
        from remedy.assistant.providers.google_gmail import get_google_gmail

        mail = get_google_gmail(home)
        if mail is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Gmail not connected. Settings → Personal assistant → Connect.",
                },
                indent=2,
            )
        if not (message_id or "").strip():
            return json.dumps(
                {"ok": False, "message": "message_id required (from mail_list)."},
                indent=2,
            )
        try:
            m = mail.get_message(message_id.strip())
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        return json.dumps(
            {
                "ok": True,
                "message": {
                    "id": m.id,
                    "subject": m.subject,
                    "from": m.from_addr,
                    "date": m.date,
                    "body": m.snippet,
                    "thread_id": m.thread_id,
                },
            },
            indent=2,
        )

    async def mail_create_draft(
        to: str = "",
        subject: str = "",
        body: str = "",
    ) -> str:
        """Create a Gmail draft (does not send)."""
        from remedy.assistant.providers.google_gmail import get_google_gmail

        mail = get_google_gmail(home)
        if mail is None:
            return json.dumps(
                {
                    "ok": False,
                    "message": "Gmail not connected. Settings → Personal assistant → Connect.",
                },
                indent=2,
            )
        if not (to or "").strip():
            return json.dumps({"ok": False, "message": "to address required"}, indent=2)
        try:
            result = mail.create_draft(
                to=to.strip(),
                subject=(subject or "").strip(),
                body=body or "",
            )
        except Exception as exc:
            return json.dumps({"ok": False, "message": str(exc)}, indent=2)
        return json.dumps(result, indent=2)

    async def budget_get() -> str:
        st = store.budget_status()
        return json.dumps(st, indent=2)

    async def budget_set(
        label: str = "",
        income_planned: float = 0.0,
        categories_json: str = "",
    ) -> str:
        """Set budget period. categories_json: [{\"name\":\"groceries\",\"planned\":400}, ...]"""
        from datetime import UTC, datetime

        lab = (label or "").strip() or datetime.now(UTC).strftime("%Y-%m")
        cats: list[dict[str, Any]] = []
        raw = (categories_json or "").strip()
        if raw:
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    cats = [c for c in parsed if isinstance(c, dict)]
            except json.JSONDecodeError:
                return "categories_json must be a JSON list of {name, planned}"
        # Accept disclaimer on first money tool use
        store.patch_prefs(money_disclaimer_accepted=True)
        period = store.set_budget(
            label=lab, income_planned=float(income_planned or 0), categories=cats
        )
        return json.dumps(
            {
                "ok": True,
                "label": period.label,
                "categories": [c.to_dict() for c in period.categories],
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"Budget set for {period.label}",
            },
            indent=2,
        )

    async def budget_tx_add(
        amount: float = 0.0,
        category: str = "misc",
        note: str = "",
        kind: str = "expense",
        date: str = "",
    ) -> str:
        store.patch_prefs(money_disclaimer_accepted=True)
        tx = store.add_tx(
            amount=float(amount),
            category=category or "misc",
            note=note,
            kind=kind or "expense",
            date=date,
        )
        return json.dumps(
            {
                "ok": True,
                "tx": tx.to_dict(),
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"Logged {tx.kind} ${tx.amount:.2f} → {tx.category}",
            },
            indent=2,
        )

    async def budget_status() -> str:
        return json.dumps(store.budget_status(), indent=2)

    async def debt_list() -> str:
        debts = [d.to_dict() for d in store.list_debts()]
        return json.dumps(
            {
                "ok": True,
                "debts": debts,
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"{len(debts)} debt(s) on file",
            },
            indent=2,
        )

    async def debt_upsert(
        name: str = "",
        balance: float = 0.0,
        apr_pct: float = 0.0,
        min_payment: float = 0.0,
        due_day: int = 0,
        note: str = "",
        id: str = "",
    ) -> str:
        store.patch_prefs(money_disclaimer_accepted=True)
        d = store.upsert_debt(
            name=name,
            balance=balance,
            apr_pct=apr_pct,
            min_payment=min_payment,
            due_day=due_day,
            note=note,
            id=id,
        )
        return json.dumps(
            {
                "ok": True,
                "debt": d.to_dict(),
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"Saved debt {d.name}",
            },
            indent=2,
        )

    async def debt_scenario(
        name: str = "",
        debt_id: str = "",
        extra_payment: float = 0.0,
    ) -> str:
        store.patch_prefs(money_disclaimer_accepted=True)
        return json.dumps(
            store.debt_scenario(
                name=name, debt_id=debt_id, extra_payment=float(extra_payment or 0)
            ),
            indent=2,
        )

    async def bill_list() -> str:
        bills = [b.to_dict() for b in store.list_bills()]
        return json.dumps(
            {
                "ok": True,
                "bills": bills,
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"{len(bills)} bill(s)",
            },
            indent=2,
        )

    async def bill_upsert(
        name: str = "",
        amount: float = 0.0,
        cadence: str = "monthly",
        next_due: str = "",
        note: str = "",
        id: str = "",
    ) -> str:
        store.patch_prefs(money_disclaimer_accepted=True)
        b = store.upsert_bill(
            name=name,
            amount=amount,
            cadence=cadence,
            next_due=next_due,
            note=note,
            id=id,
        )
        return json.dumps(
            {
                "ok": True,
                "bill": b.to_dict(),
                "disclaimer": MONEY_DISCLAIMER_SHORT,
                "message": f"Saved bill {b.name}",
            },
            indent=2,
        )

    async def money_disclaimer() -> str:
        return MONEY_DISCLAIMER_FULL

    reg = runtime.tool_registry
    reg.register_builtin_handler(
        "assistant_accounts",
        "Personal assistant status: linked accounts (planned/connected), budget/debt counts, brief prefs.",
        assistant_accounts,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "assistant_brief",
        "On-demand brief: budget/bills/debts + open goals. Calendar/mail when accounts linked.",
        assistant_brief,
        {
            "type": "object",
            "properties": {
                "hint": {"type": "string", "description": "Optional focus"},
            },
        },
    )
    reg.register_builtin_handler(
        "budget_get",
        "Get current budget period and category remaining (organization tool, not advice).",
        budget_get,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "budget_set",
        "Set/replace budget for a month label. categories_json: JSON list of {name, planned}.",
        budget_set,
        {
            "type": "object",
            "properties": {
                "label": {"type": "string", "description": "e.g. 2026-07"},
                "income_planned": {"type": "number"},
                "categories_json": {
                    "type": "string",
                    "description": '[{"name":"groceries","planned":400}]',
                },
            },
        },
    )
    reg.register_builtin_handler(
        "budget_tx_add",
        "Log an expense or income against the budget (user-entered amounts).",
        budget_tx_add,
        {
            "type": "object",
            "properties": {
                "amount": {"type": "number"},
                "category": {"type": "string"},
                "note": {"type": "string"},
                "kind": {"type": "string", "description": "expense | income"},
                "date": {"type": "string", "description": "YYYY-MM-DD optional"},
            },
            "required": ["amount"],
        },
    )
    reg.register_builtin_handler(
        "budget_status",
        "Spent vs planned by category (organization only).",
        budget_status,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "debt_list",
        "List user-entered debts (balances/APRs you stored — not credit pulls).",
        debt_list,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "debt_upsert",
        "Add/update a debt the user reports (name, balance, apr_pct, min_payment, due_day).",
        debt_upsert,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "balance": {"type": "number"},
                "apr_pct": {"type": "number"},
                "min_payment": {"type": "number"},
                "due_day": {"type": "integer"},
                "note": {"type": "string"},
                "id": {"type": "string"},
            },
            "required": ["name"],
        },
    )
    reg.register_builtin_handler(
        "debt_scenario",
        "Illustrative payoff months if paying min+extra (NOT financial advice).",
        debt_scenario,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "debt_id": {"type": "string"},
                "extra_payment": {"type": "number"},
            },
        },
    )
    reg.register_builtin_handler(
        "bill_list",
        "List bills/due dates the user logged.",
        bill_list,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "bill_upsert",
        "Add/update a bill (name, amount, cadence monthly|weekly|yearly, next_due YYYY-MM-DD).",
        bill_upsert,
        {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "amount": {"type": "number"},
                "cadence": {"type": "string"},
                "next_due": {"type": "string"},
                "note": {"type": "string"},
                "id": {"type": "string"},
            },
            "required": ["name"],
        },
    )
    reg.register_builtin_handler(
        "money_disclaimer",
        "Show the full budget/debt tools disclaimer (organization, not advice).",
        money_disclaimer,
        {"type": "object", "properties": {}},
    )
    reg.register_builtin_handler(
        "calendar_list_events",
        "List Google Calendar primary events (needs Connect Google OAuth). days default 7.",
        calendar_list_events,
        {
            "type": "object",
            "properties": {
                "days": {"type": "integer", "description": "Lookahead days (1–31), default 7"},
                "time_min": {"type": "string", "description": "Optional ISO start"},
                "time_max": {"type": "string", "description": "Optional ISO end"},
            },
        },
    )
    reg.register_builtin_handler(
        "calendar_create_event",
        "Create a Google Calendar event on primary calendar (official API, not browser login).",
        calendar_create_event,
        {
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start": {
                    "type": "string",
                    "description": "ISO datetime or YYYY-MM-DD (all-day)",
                },
                "end": {
                    "type": "string",
                    "description": "ISO datetime or YYYY-MM-DD (all-day exclusive end)",
                },
                "description": {"type": "string"},
            },
            "required": ["title", "start", "end"],
        },
    )
    reg.register_builtin_handler(
        "mail_list",
        "List Gmail messages (query e.g. in:inbox, from:x). Needs Connect Google (Gmail).",
        mail_list,
        {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Gmail search query (default in:inbox)",
                },
                "limit": {"type": "integer", "description": "Max messages 1–50"},
            },
        },
    )
    reg.register_builtin_handler(
        "mail_get",
        "Read one Gmail message by id from mail_list (plain text / snippet).",
        mail_get,
        {
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
            },
            "required": ["message_id"],
        },
    )
    reg.register_builtin_handler(
        "mail_create_draft",
        "Create a Gmail draft (does not send). Needs Connect Google (Gmail).",
        mail_create_draft,
        {
            "type": "object",
            "properties": {
                "to": {"type": "string"},
                "subject": {"type": "string"},
                "body": {"type": "string"},
            },
            "required": ["to"],
        },
    )
