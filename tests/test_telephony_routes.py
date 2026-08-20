"""Telephony HTTP: status, terms, line pick — no real call, no network."""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def client(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    monkeypatch.setenv("REMEDY_API_AUTH", "0")
    from remedy.interfaces.api import create_app

    return TestClient(create_app())


def test_telephony_status_is_plain(client: TestClient):
    r = client.get("/api/telephony/status")
    assert r.status_code == 200
    data = r.json()
    assert data["real_line"] is False
    assert data["phase"] == 0
    assert data["loopback"] is True
    assert data["terms"]["agreed"] is False
    assert "lines" in data and len(data["lines"]) >= 1
    assert "sip" in {row["name"] for row in data["lines"]}
    assert "message" in data


def test_telephony_terms_accept_and_withdraw(client: TestClient):
    r = client.post("/api/telephony/terms", json={"accept": True})
    assert r.status_code == 200
    assert r.json()["agreed"] is True
    st = client.get("/api/telephony/status").json()
    assert st["terms"]["agreed"] is True
    client.post("/api/telephony/terms", json={"accept": False})
    st2 = client.get("/api/telephony/status").json()
    assert st2["terms"]["agreed"] is False


def test_telephony_choose_line(client: TestClient):
    r = client.post("/api/telephony/choose", json={"name": "sip"})
    assert r.status_code == 200
    assert r.json()["chosen"] == "sip"
    st = client.get("/api/telephony/status").json()
    assert st["chosen"] == "sip"
