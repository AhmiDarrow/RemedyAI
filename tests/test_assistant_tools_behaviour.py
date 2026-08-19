"""The money and brief tools, driven the way the model drives them.

464 statements at 28% covered, and the manual now tells owners these work — so
they should be checked, not just documented. Every figure here is one the owner
entered; nothing is advice, and the tools say so themselves.
"""

from __future__ import annotations

import json

import pytest

from remedy.core.agent_assistant_tools import register_assistant_tools


class _Registry:
    def __init__(self) -> None:
        self.handlers: dict[str, object] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.handlers[name] = handler

    def __getattr__(self, _name):
        return lambda *a, **kw: None


class _Config:
    def __init__(self, home: str) -> None:
        self.home_dir = home

    def __getattr__(self, _name):
        return None


class _Runtime:
    def __init__(self, home: str) -> None:
        self.tool_registry = _Registry()
        self.config = _Config(home)

    def __getattr__(self, _name):
        return None


@pytest.fixture
def tools(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    rt = _Runtime(str(tmp_path))
    register_assistant_tools(rt)
    return rt.tool_registry.handlers


async def _json(tools, _tool, **kw):
    """``_tool`` is underscored so a tool argument called ``name`` can pass."""
    return json.loads(await tools[_tool](**kw))


def test_the_expected_tools_are_registered(tools):
    for name in (
        "budget_set", "budget_get", "budget_status", "budget_tx_add",
        "bill_upsert", "bill_list", "debt_upsert", "debt_list", "debt_scenario",
        "assistant_accounts", "assistant_brief", "money_disclaimer",
        "mail_connect", "mail_disconnect", "mail_status",
    ):
        assert name in tools, f"{name} is no longer registered"


@pytest.mark.asyncio
async def test_a_budget_round_trips(tools):
    out = await _json(
        tools, "budget_set", label="2026-09", income_planned=3000,
        categories_json=json.dumps([{"name": "groceries", "planned": 400}]),
    )
    assert out["ok"]
    got = await _json(tools, "budget_get")
    assert got["label"] == "2026-09"


@pytest.mark.asyncio
async def test_spending_moves_the_remaining_figure(tools):
    await _json(
        tools, "budget_set", label="2026-09", income_planned=3000,
        categories_json=json.dumps([{"name": "groceries", "planned": 400}]),
    )
    await _json(tools, "budget_tx_add", amount=52.40, category="groceries", kind="expense")
    status = await _json(tools, "budget_status")
    row = next(c for c in status["categories"] if c["category"] == "groceries")
    assert row["spent"] == pytest.approx(52.40)
    assert row["remaining"] == pytest.approx(347.60)


@pytest.mark.asyncio
async def test_a_bill_survives_a_round_trip(tools):
    await _json(tools, "bill_upsert", name="rent", amount=1200,
                cadence="monthly", next_due="2026-09-01")
    listed = await _json(tools, "bill_list")
    names = [b["name"] for b in listed.get("bills", listed if isinstance(listed, list) else [])]
    assert "rent" in names


@pytest.mark.asyncio
async def test_the_payoff_scenario_is_arithmetic_not_advice(tools):
    await _json(tools, "debt_upsert", name="card", balance=2400,
                apr_pct=19.9, min_payment=60)
    out = await _json(tools, "debt_scenario", name="card", extra_payment=100)

    assert out["ok"]
    assert out["monthly_payment"] == pytest.approx(160.0)  # min + extra
    assert out["months"] > 0
    blob = json.dumps(out).lower()
    assert "advice" in blob or "not financial" in blob or "illustrat" in blob, (
        "the payoff figure must carry its own disclaimer"
    )


@pytest.mark.asyncio
async def test_paying_more_clears_the_debt_sooner(tools):
    await _json(tools, "debt_upsert", name="card", balance=2400,
                apr_pct=19.9, min_payment=60)
    slow = await _json(tools, "debt_scenario", name="card", extra_payment=0)
    fast = await _json(tools, "debt_scenario", name="card", extra_payment=200)
    assert fast["months"] < slow["months"]


@pytest.mark.asyncio
async def test_the_disclaimer_is_always_available(tools):
    text = await tools["money_disclaimer"]()
    assert "advice" in text.lower()


@pytest.mark.asyncio
async def test_the_brief_works_with_nothing_configured(tools):
    """It is the first thing a new owner asks for, before anything is set up."""
    text = await tools["assistant_brief"]()
    assert isinstance(text, str) and text.strip()


@pytest.mark.asyncio
async def test_status_never_leaks_a_stored_secret(tools, tmp_path):
    from remedy.interfaces.secret_store import set_provider_secret

    set_provider_secret("mail_app_password", "hunter2-app-password", tmp_path)
    set_provider_secret("mail_address", "someone@yahoo.com", tmp_path)
    blob = await tools["assistant_accounts"]()
    assert "hunter2-app-password" not in blob
    assert "someone@yahoo.com" not in blob


@pytest.mark.asyncio
async def test_an_unknown_mail_domain_is_refused_before_storing(tools):
    out = await _json(tools, "mail_connect", address="a@example.invalid",
                      app_password="abcd efgh ijkl mnop")
    assert out["ok"] is False
    assert "servers" in out["message"].lower() or "support" in out["message"].lower()


@pytest.mark.asyncio
async def test_mail_status_answers_with_no_mailbox(tools):
    out = await tools["mail_status"]()
    assert isinstance(out, str) and out.strip()
