"""Local-first store for PA prefs, budget, debts, bills (no bank link required)."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from remedy.assistant.disclaimer import MONEY_DISCLAIMER_SHORT
from remedy.assistant.models import (
    AssistantPrefs,
    BillItem,
    BudgetCategory,
    BudgetPeriod,
    BudgetTx,
    DebtItem,
    LinkedAccount,
    _id,
    _now,
)

_STORE_NAME = "assistant.json"
_lock = threading.Lock()
_singleton: AssistantStore | None = None


def _home(home: Path | str | None = None) -> Path:
    if home is not None and str(home).strip():
        return Path(home).expanduser().resolve()
    return (Path.home() / ".remedy").resolve()


class AssistantStore:
    """Filesystem store under ``~/.remedy/assistant.json``."""

    def __init__(self, home_dir: Path | str | None = None) -> None:
        self.home = _home(home_dir)
        self.path = self.home / _STORE_NAME
        self.home.mkdir(parents=True, exist_ok=True)
        self._data: dict[str, Any] = self._load()

    def _load(self) -> dict[str, Any]:
        if not self.path.is_file():
            return self._empty()
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                return raw
        except (OSError, json.JSONDecodeError):
            pass
        return self._empty()

    def _empty(self) -> dict[str, Any]:
        return {
            "version": 1,
            "prefs": AssistantPrefs().to_dict(),
            "accounts": [],
            "budget": None,
            "debts": [],
            "bills": [],
            "updated_at": _now(),
        }

    def _save(self) -> None:
        self._data["updated_at"] = _now()
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ── prefs / accounts ──────────────────────────────────────────────────

    def get_prefs(self) -> AssistantPrefs:
        return AssistantPrefs.from_dict(self._data.get("prefs"))

    def set_prefs(self, prefs: AssistantPrefs) -> AssistantPrefs:
        with _lock:
            self._data["prefs"] = prefs.to_dict()
            self._save()
        return prefs

    def patch_prefs(self, **kwargs: Any) -> AssistantPrefs:
        p = self.get_prefs()
        for k, v in kwargs.items():
            if k == "brief" and isinstance(v, dict):
                from remedy.assistant.models import BriefPrefs

                p.brief = BriefPrefs.from_dict({**p.brief.to_dict(), **v})
            elif hasattr(p, k) and v is not None:
                setattr(p, k, v)
        return self.set_prefs(p)

    def list_accounts(self) -> list[LinkedAccount]:
        return [
            LinkedAccount.from_dict(a)
            for a in (self._data.get("accounts") or [])
            if isinstance(a, dict)
        ]

    def upsert_account(self, acct: LinkedAccount) -> LinkedAccount:
        with _lock:
            accounts = self.list_accounts()
            found = False
            out: list[dict[str, Any]] = []
            for a in accounts:
                if a.id == acct.id or (
                    a.provider == acct.provider and a.email == acct.email and acct.email
                ):
                    out.append(acct.to_dict())
                    found = True
                else:
                    out.append(a.to_dict())
            if not found:
                out.append(acct.to_dict())
            self._data["accounts"] = out
            self._save()
        return acct

    def accounts_public(self) -> list[dict[str, Any]]:
        """Safe status for Settings / tools (no tokens)."""
        return [
            {
                "id": a.id,
                "provider": a.provider,
                "email": a.email,
                "capabilities": a.capabilities,
                "status": a.status,
                "last_sync": a.last_sync,
                "error": a.error,
            }
            for a in self.list_accounts()
        ]

    # ── budget ────────────────────────────────────────────────────────────

    def get_budget(self) -> BudgetPeriod | None:
        raw = self._data.get("budget")
        if isinstance(raw, dict) and raw.get("label"):
            return BudgetPeriod.from_dict(raw)
        return None

    def set_budget(
        self,
        *,
        label: str,
        income_planned: float = 0.0,
        categories: list[dict[str, Any]] | None = None,
    ) -> BudgetPeriod:
        existing = self.get_budget()
        txs = existing.transactions if existing and existing.label == label else []
        cats = [
            BudgetCategory.from_dict(c) if isinstance(c, dict) else c
            for c in (categories or [])
        ]
        if not cats and existing and existing.label == label:
            cats = existing.categories
        period = BudgetPeriod(
            id=existing.id if existing and existing.label == label else _id("bud_"),
            label=label,
            income_planned=float(income_planned),
            categories=cats,
            transactions=list(txs),
        )
        with _lock:
            self._data["budget"] = period.to_dict()
            self._save()
        return period

    def add_tx(
        self,
        *,
        amount: float,
        category: str,
        note: str = "",
        kind: str = "expense",
        date: str = "",
    ) -> BudgetTx:
        bud = self.get_budget()
        if bud is None:
            from datetime import UTC, datetime

            label = datetime.now(UTC).strftime("%Y-%m")
            bud = self.set_budget(label=label, income_planned=0.0, categories=[])
        tx = BudgetTx(
            id=_id("tx_"),
            amount=float(amount),
            category=str(category or "misc"),
            note=str(note or ""),
            kind=str(kind or "expense"),
            date=str(date or ""),
        )
        bud.transactions.append(tx)
        with _lock:
            self._data["budget"] = bud.to_dict()
            self._save()
        return tx

    def budget_status(self) -> dict[str, Any]:
        bud = self.get_budget()
        if bud is None:
            return {
                "ok": True,
                "message": "No budget set. Use budget_set for a month label and categories.",
                "disclaimer": MONEY_DISCLAIMER_SHORT,
            }
        spent: dict[str, float] = {}
        income = 0.0
        for t in bud.transactions:
            if t.kind == "income":
                income += t.amount
            else:
                spent[t.category] = spent.get(t.category, 0.0) + abs(t.amount)
        rows = []
        for c in bud.categories:
            s = spent.get(c.name, 0.0)
            rows.append(
                {
                    "category": c.name,
                    "planned": c.planned,
                    "spent": round(s, 2),
                    "remaining": round(c.planned - s, 2),
                }
            )
        # orphans
        for cat, s in spent.items():
            if not any(c.name == cat for c in bud.categories):
                rows.append(
                    {
                        "category": cat,
                        "planned": 0.0,
                        "spent": round(s, 2),
                        "remaining": round(-s, 2),
                    }
                )
        return {
            "ok": True,
            "label": bud.label,
            "income_planned": bud.income_planned,
            "income_logged": round(income, 2),
            "categories": rows,
            "tx_count": len(bud.transactions),
            "disclaimer": MONEY_DISCLAIMER_SHORT,
            "message": f"Budget {bud.label}: {len(rows)} categories, {len(bud.transactions)} transactions",
        }

    # ── debts ─────────────────────────────────────────────────────────────

    def list_debts(self) -> list[DebtItem]:
        return [
            DebtItem.from_dict(d)
            for d in (self._data.get("debts") or [])
            if isinstance(d, dict)
        ]

    def upsert_debt(self, **kwargs: Any) -> DebtItem:
        name = str(kwargs.get("name") or "").strip() or "Debt"
        debts = self.list_debts()
        existing = next(
            (d for d in debts if d.id == kwargs.get("id") or d.name.lower() == name.lower()),
            None,
        )
        if existing:
            item = DebtItem(
                id=existing.id,
                name=name,
                balance=float(kwargs.get("balance", existing.balance)),
                apr_pct=float(kwargs.get("apr_pct", kwargs.get("apr", existing.apr_pct))),
                min_payment=float(kwargs.get("min_payment", existing.min_payment)),
                due_day=int(kwargs.get("due_day", existing.due_day) or 0),
                note=str(kwargs.get("note", existing.note) or ""),
            )
        else:
            item = DebtItem(
                id=_id("debt_"),
                name=name,
                balance=float(kwargs.get("balance") or 0),
                apr_pct=float(kwargs.get("apr_pct") or kwargs.get("apr") or 0),
                min_payment=float(kwargs.get("min_payment") or 0),
                due_day=int(kwargs.get("due_day") or 0),
                note=str(kwargs.get("note") or ""),
            )
        with _lock:
            others = [d.to_dict() for d in debts if d.id != item.id]
            others.append(item.to_dict())
            self._data["debts"] = others
            self._save()
        return item

    def debt_scenario(
        self,
        *,
        debt_id: str = "",
        name: str = "",
        extra_payment: float = 0.0,
    ) -> dict[str, Any]:
        """Simple amortization illustration — not financial advice."""
        debts = self.list_debts()
        item = None
        if debt_id:
            item = next((d for d in debts if d.id == debt_id), None)
        if item is None and name:
            item = next((d for d in debts if d.name.lower() == name.lower()), None)
        if item is None and len(debts) == 1:
            item = debts[0]
        if item is None:
            return {
                "ok": False,
                "message": "No matching debt — debt_upsert first",
                "disclaimer": MONEY_DISCLAIMER_SHORT,
            }
        bal = max(0.0, float(item.balance))
        apr = max(0.0, float(item.apr_pct)) / 100.0
        monthly_rate = apr / 12.0
        pay = max(0.0, float(item.min_payment) + float(extra_payment or 0))
        if pay <= 0:
            return {
                "ok": False,
                "message": "Set min_payment and/or extra_payment > 0",
                "disclaimer": MONEY_DISCLAIMER_SHORT,
            }
        months = 0
        total_paid = 0.0
        b = bal
        # Cap runaway loops
        while b > 0.01 and months < 600:
            interest = b * monthly_rate
            principal = pay - interest
            if principal <= 0:
                return {
                    "ok": True,
                    "debt": item.name,
                    "message": (
                        f"Scenario: payment ${pay:.2f}/mo does not cover interest "
                        f"(~${interest:.2f}/mo at {item.apr_pct:.2f}% APR). "
                        "Illustration only — not advice."
                    ),
                    "pays_off": False,
                    "disclaimer": MONEY_DISCLAIMER_SHORT,
                }
            b = b + interest - pay
            total_paid += pay
            months += 1
            if b < 0:
                total_paid += b  # last overpay
                b = 0
        return {
            "ok": True,
            "debt": item.name,
            "starting_balance": bal,
            "monthly_payment": round(pay, 2),
            "extra_payment": round(float(extra_payment or 0), 2),
            "months": months,
            "total_paid_approx": round(total_paid, 2),
            "pays_off": b <= 0.01,
            "message": (
                f"Scenario for {item.name}: ~{months} months at ${pay:.2f}/mo "
                f"(illustration only — not financial advice)."
            ),
            "disclaimer": MONEY_DISCLAIMER_SHORT,
        }

    # ── bills ─────────────────────────────────────────────────────────────

    def list_bills(self) -> list[BillItem]:
        return [
            BillItem.from_dict(b)
            for b in (self._data.get("bills") or [])
            if isinstance(b, dict)
        ]

    def upsert_bill(self, **kwargs: Any) -> BillItem:
        name = str(kwargs.get("name") or "").strip() or "Bill"
        bills = self.list_bills()
        existing = next(
            (b for b in bills if b.id == kwargs.get("id") or b.name.lower() == name.lower()),
            None,
        )
        if existing:
            item = BillItem(
                id=existing.id,
                name=name,
                amount=float(kwargs.get("amount", existing.amount)),
                cadence=str(kwargs.get("cadence", existing.cadence) or "monthly"),
                next_due=str(kwargs.get("next_due", existing.next_due) or ""),
                note=str(kwargs.get("note", existing.note) or ""),
            )
        else:
            item = BillItem(
                id=_id("bill_"),
                name=name,
                amount=float(kwargs.get("amount") or 0),
                cadence=str(kwargs.get("cadence") or "monthly"),
                next_due=str(kwargs.get("next_due") or ""),
                note=str(kwargs.get("note") or ""),
            )
        with _lock:
            others = [b.to_dict() for b in bills if b.id != item.id]
            others.append(item.to_dict())
            self._data["bills"] = others
            self._save()
        return item

    def public_status(self) -> dict[str, Any]:
        prefs = self.get_prefs()
        google_status = "planned"
        try:
            from remedy.assistant.google_oauth import load_tokens

            tok = load_tokens(self.home)
            if tok.connected:
                google_status = "connected"
            else:
                from remedy.assistant.google_oauth import load_app_config

                google_status = "ready" if load_app_config(self.home).configured() else "planned"
        except Exception:
            pass
        return {
            "enabled": prefs.enabled,
            "timezone": prefs.timezone,
            "money_disclaimer_accepted": prefs.money_disclaimer_accepted,
            "money_disclaimer": MONEY_DISCLAIMER_SHORT,
            "brief": prefs.brief.to_dict(),
            "accounts": self.accounts_public(),
            "has_budget": self.get_budget() is not None,
            "debt_count": len(self.list_debts()),
            "bill_count": len(self.list_bills()),
            "providers_planned": [
                {
                    "id": "google",
                    "name": "Gmail",
                    "status": google_status,
                },
                {"id": "microsoft", "name": "Microsoft (Outlook)", "status": "planned"},
                {"id": "yahoo", "name": "Yahoo Mail", "status": "planned"},
            ],
        }


def get_assistant_store(home_dir: Path | str | None = None) -> AssistantStore:
    """Return the PA store for *home_dir* (defaults to ``~/.remedy``).

    Rebinds the process singleton when *home_dir* resolves to a different path
    (tests and multi-home setups). Prefer passing *home_dir* from config.
    """
    global _singleton
    resolved = _home(home_dir)
    with _lock:
        if _singleton is None or Path(_singleton.home) != resolved:
            _singleton = AssistantStore(home_dir=resolved)
        return _singleton


def reset_assistant_store() -> None:
    """Clear the process singleton (tests only)."""
    global _singleton
    with _lock:
        _singleton = None
