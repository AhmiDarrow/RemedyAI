"""Personal assistant store + settings API (Phase 0 skeleton)."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from remedy.assistant.disclaimer import MONEY_DISCLAIMER_SHORT
from remedy.assistant.store import (
    AssistantStore,
    get_assistant_store,
    reset_assistant_store,
)
from remedy.interfaces.api import create_app


@pytest.fixture(autouse=True)
def _reset_store():
    reset_assistant_store()
    yield
    reset_assistant_store()


def test_store_budget_and_debt(tmp_path):
    store = AssistantStore(home_dir=tmp_path)
    store.set_budget(
        label="2026-07",
        income_planned=4000,
        categories=[{"name": "groceries", "planned": 400}],
    )
    store.add_tx(amount=50, category="groceries", note="milk")
    st = store.budget_status()
    assert st["ok"] is True
    assert st["label"] == "2026-07"
    row = next(r for r in st["categories"] if r["category"] == "groceries")
    assert row["spent"] == 50
    assert row["remaining"] == 350
    assert MONEY_DISCLAIMER_SHORT in st["disclaimer"]

    d = store.upsert_debt(name="Card", balance=1000, apr_pct=20, min_payment=50)
    assert d.name == "Card"
    sc = store.debt_scenario(name="Card", extra_payment=25)
    assert sc["ok"] is True
    assert sc["pays_off"] is True
    assert "not financial advice" in sc["message"].lower() or "illustration" in sc[
        "message"
    ].lower()


def test_store_patch_prefs_and_public_status(tmp_path):
    store = AssistantStore(home_dir=tmp_path)
    store.patch_prefs(
        enabled=True,
        money_disclaimer_accepted=True,
        brief={"enabled": True, "hour_local": 8, "include_mail": False},
    )
    prefs = store.get_prefs()
    assert prefs.money_disclaimer_accepted is True
    assert prefs.brief.hour_local == 8
    assert prefs.brief.include_mail is False
    pub = store.public_status()
    assert pub["enabled"] is True
    assert pub["has_budget"] is False
    assert pub["debt_count"] == 0
    assert any(p["id"] == "google" for p in pub["providers_planned"])
    assert (tmp_path / "assistant.json").is_file()


def test_get_assistant_store_rebinds_home(tmp_path):
    a = tmp_path / "a"
    b = tmp_path / "b"
    a.mkdir()
    b.mkdir()
    s1 = get_assistant_store(a)
    s1.patch_prefs(timezone="UTC")
    s2 = get_assistant_store(b)
    assert s2.home == b.resolve()
    assert s2.get_prefs().timezone == ""
    s1b = get_assistant_store(a)
    assert s1b.get_prefs().timezone == "UTC"


def test_settings_get_includes_assistant(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        'name = "Remedy"\nenabled_channels = ["cli"]\nsetup_completed = true\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(home))
    from remedy.interfaces import api_support

    monkeypatch.setattr(api_support, "_default_config_path", lambda: home / "config.toml")
    monkeypatch.setattr(api_support, "_find_config_path", lambda: home / "config.toml")
    reset_assistant_store()
    client = TestClient(create_app())
    r = client.get("/api/settings")
    assert r.status_code == 200
    data = r.json()
    assert "assistant" in data
    assert isinstance(data["assistant"], dict)
    assert "providers_planned" in data["assistant"]
    assert data["assistant"].get("enabled") is True


def test_settings_put_assistant_prefs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    (home / "config.toml").write_text(
        f'name = "Remedy"\nenabled_channels = ["cli"]\nsetup_completed = true\nhome_dir = "{home.as_posix()}"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(home))
    from remedy.interfaces import api_support

    monkeypatch.setattr(api_support, "_default_config_path", lambda: home / "config.toml")
    monkeypatch.setattr(api_support, "_find_config_path", lambda: home / "config.toml")
    reset_assistant_store()
    client = TestClient(create_app())
    r = client.put(
        "/api/settings",
        json={
            "assistant": {
                "enabled": True,
                "money_disclaimer_accepted": True,
                "brief": {"enabled": True, "hour_local": 9, "include_budget": True},
            }
        },
    )
    assert r.status_code == 200, r.text
    reset_assistant_store()
    store = get_assistant_store(home)
    prefs = store.get_prefs()
    assert prefs.money_disclaimer_accepted is True
    assert prefs.brief.enabled is True
    assert prefs.brief.hour_local == 9
    # File on disk
    raw = json.loads((home / "assistant.json").read_text(encoding="utf-8"))
    assert raw["prefs"]["money_disclaimer_accepted"] is True

    r2 = client.get("/api/settings")
    assert r2.status_code == 200
    assert r2.json()["assistant"]["money_disclaimer_accepted"] is True
    assert r2.json()["assistant"]["brief"]["hour_local"] == 9
