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
                "None connected yet. Google/Microsoft/Yahoo calendar & mail: Settings → "
                "Personal assistant (OAuth coming next)."
            )
        else:
            for a in accts:
                parts.append(f"- {a.get('provider')}: {a.get('status')} {a.get('email')}")
        parts.append("")
        parts.append(f"_Note: {MONEY_DISCLAIMER_SHORT}_")
        if hint:
            parts.append(f"\n(User hint: {hint[:200]})")
        return "\n".join(parts)

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
