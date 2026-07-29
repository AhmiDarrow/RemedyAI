"""Data models for personal assistant prefs, accounts, budget, debts, bills."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _id(prefix: str = "") -> str:
    return f"{prefix}{uuid4().hex[:12]}" if prefix else uuid4().hex[:12]


@dataclass
class LinkedAccount:
    """OAuth-linked productivity account (Google, Microsoft, …). Phase 0: stubs."""

    id: str
    provider: str  # google | microsoft | yahoo
    email: str = ""
    capabilities: list[str] = field(default_factory=list)  # calendar, mail
    status: str = "disconnected"  # disconnected | connected | error
    last_sync: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LinkedAccount:
        return cls(
            id=str(raw.get("id") or _id("acct_")),
            provider=str(raw.get("provider") or ""),
            email=str(raw.get("email") or ""),
            capabilities=list(raw.get("capabilities") or []),
            status=str(raw.get("status") or "disconnected"),
            last_sync=str(raw.get("last_sync") or ""),
            error=str(raw.get("error") or ""),
        )


@dataclass
class BriefPrefs:
    enabled: bool = False
    hour_local: int = 7  # 0–23
    quiet_start: int = 22
    quiet_end: int = 7
    include_calendar: bool = True
    include_mail: bool = True
    include_goals: bool = True
    include_budget: bool = True
    messenger_delivery: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> BriefPrefs:
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", False)),
            hour_local=int(raw.get("hour_local", 7)),
            quiet_start=int(raw.get("quiet_start", 22)),
            quiet_end=int(raw.get("quiet_end", 7)),
            include_calendar=bool(raw.get("include_calendar", True)),
            include_mail=bool(raw.get("include_mail", True)),
            include_goals=bool(raw.get("include_goals", True)),
            include_budget=bool(raw.get("include_budget", True)),
            messenger_delivery=bool(raw.get("messenger_delivery", False)),
        )


@dataclass
class AssistantPrefs:
    """User prefs for PA (config.toml + optional store merge)."""

    enabled: bool = True
    timezone: str = ""  # empty → system / profile later
    money_disclaimer_accepted: bool = False
    # AI + connected-account privacy (required before OAuth Connect)
    privacy_ai_accepted: bool = False
    account_access_accepted: bool = False
    default_calendar_account: str = ""
    default_mail_account: str = ""
    brief: BriefPrefs = field(default_factory=BriefPrefs)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any] | None) -> AssistantPrefs:
        raw = raw or {}
        return cls(
            enabled=bool(raw.get("enabled", True)),
            timezone=str(raw.get("timezone") or ""),
            money_disclaimer_accepted=bool(raw.get("money_disclaimer_accepted", False)),
            privacy_ai_accepted=bool(raw.get("privacy_ai_accepted", False)),
            account_access_accepted=bool(raw.get("account_access_accepted", False)),
            default_calendar_account=str(raw.get("default_calendar_account") or ""),
            default_mail_account=str(raw.get("default_mail_account") or ""),
            brief=BriefPrefs.from_dict(
                raw.get("brief") if isinstance(raw.get("brief"), dict) else raw
            ),
        )


@dataclass
class BudgetCategory:
    name: str
    planned: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {"name": self.name, "planned": float(self.planned)}

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BudgetCategory:
        return cls(
            name=str(raw.get("name") or "misc"),
            planned=float(raw.get("planned") or 0),
        )


@dataclass
class BudgetTx:
    id: str
    amount: float
    category: str
    note: str = ""
    kind: str = "expense"  # expense | income
    date: str = ""
    created_at: str = field(default_factory=_now)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BudgetTx:
        return cls(
            id=str(raw.get("id") or _id("tx_")),
            amount=float(raw.get("amount") or 0),
            category=str(raw.get("category") or "misc"),
            note=str(raw.get("note") or ""),
            kind=str(raw.get("kind") or "expense"),
            date=str(raw.get("date") or ""),
            created_at=str(raw.get("created_at") or _now()),
        )


@dataclass
class BudgetPeriod:
    id: str
    label: str  # e.g. 2026-07
    income_planned: float = 0.0
    categories: list[BudgetCategory] = field(default_factory=list)
    transactions: list[BudgetTx] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "label": self.label,
            "income_planned": self.income_planned,
            "categories": [c.to_dict() for c in self.categories],
            "transactions": [t.to_dict() for t in self.transactions],
        }

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BudgetPeriod:
        return cls(
            id=str(raw.get("id") or _id("bud_")),
            label=str(raw.get("label") or ""),
            income_planned=float(raw.get("income_planned") or 0),
            categories=[
                BudgetCategory.from_dict(c)
                for c in (raw.get("categories") or [])
                if isinstance(c, dict)
            ],
            transactions=[
                BudgetTx.from_dict(t)
                for t in (raw.get("transactions") or [])
                if isinstance(t, dict)
            ],
        )


@dataclass
class DebtItem:
    id: str
    name: str
    balance: float = 0.0
    apr_pct: float = 0.0
    min_payment: float = 0.0
    due_day: int = 0  # 1–31, 0 = unset
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> DebtItem:
        return cls(
            id=str(raw.get("id") or _id("debt_")),
            name=str(raw.get("name") or "Debt"),
            balance=float(raw.get("balance") or 0),
            apr_pct=float(raw.get("apr_pct") or raw.get("apr") or 0),
            min_payment=float(raw.get("min_payment") or 0),
            due_day=int(raw.get("due_day") or 0),
            note=str(raw.get("note") or ""),
        )


@dataclass
class BillItem:
    id: str
    name: str
    amount: float = 0.0
    cadence: str = "monthly"  # monthly | weekly | yearly | once
    next_due: str = ""  # YYYY-MM-DD
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> BillItem:
        return cls(
            id=str(raw.get("id") or _id("bill_")),
            name=str(raw.get("name") or "Bill"),
            amount=float(raw.get("amount") or 0),
            cadence=str(raw.get("cadence") or "monthly"),
            next_due=str(raw.get("next_due") or ""),
            note=str(raw.get("note") or ""),
        )
