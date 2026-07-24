"""Local API authentication (Phase A)."""

from __future__ import annotations

import os

import pytest
from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.interfaces.local_auth import ensure_local_api_token, token_path


@pytest.fixture
def auth_on(monkeypatch):
    monkeypatch.setenv("REMEDY_API_AUTH", "1")
    monkeypatch.delenv("REMEDY_API_KEY", raising=False)
    yield
    monkeypatch.setenv("REMEDY_API_AUTH", "0")


def test_ensure_token_generates_and_persists(tmp_path, auth_on):
    home = tmp_path / "home"
    home.mkdir()
    t1 = ensure_local_api_token(home)
    assert len(t1) >= 16
    assert token_path(home).is_file()
    t2 = ensure_local_api_token(home)
    assert t1 == t2


def test_auth_middleware_401_without_token(auth_on, tmp_path):
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/skills")
    assert r.status_code == 401


def test_auth_middleware_ok_with_bearer(auth_on, tmp_path):
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/skills", headers={"Authorization": f"Bearer {tok}"})
    assert r.status_code == 200


def test_status_public(auth_on, tmp_path):
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/status")
    assert r.status_code == 200


def test_bootstrap_loopback(auth_on, tmp_path):
    tok = ensure_local_api_token(tmp_path)
    app = create_app(api_key=tok)
    client = TestClient(app)
    r = client.get("/api/auth/local-bootstrap")
    assert r.status_code == 200
    assert r.json()["token"] == tok


def test_auth_disabled_empty_key(monkeypatch):
    monkeypatch.setenv("REMEDY_API_AUTH", "0")
    app = create_app(api_key="")
    client = TestClient(app)
    r = client.get("/api/skills")
    assert r.status_code == 200
