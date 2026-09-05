"""Local API authentication helpers (token files).

HTTP auth / CORS / local-bootstrap live in Go ``native/go/httpapi``
(``api_test.go``, ``settings_test.go``). The FastAPI ``create_app`` harness
is gone.
"""

from __future__ import annotations

import pytest

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


def test_ensure_token_does_not_clobber_foreign_dpapi(tmp_path, auth_on):
    """Linux/WSL must not overwrite a Windows-sealed token file."""
    home = tmp_path / "home"
    home.mkdir()
    path = token_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    envelope = '{"v": 2, "dpapi": "AAAA"}'
    path.write_text(envelope, encoding="utf-8")
    tok = ensure_local_api_token(home)
    assert len(tok) >= 16
    assert path.read_text(encoding="utf-8") == envelope
    alt = path.with_name("local_api_token.posix")
    assert alt.is_file()
    assert ensure_local_api_token(home) == tok


def test_local_api_token_dpapi_or_plain_roundtrip(tmp_path, auth_on):
    """Bearer must round-trip; on Windows prefer DPAPI envelope (opaque on disk)."""
    import json
    import sys

    from remedy.interfaces.local_auth import (
        load_local_api_token,
        token_encoding,
    )

    home = tmp_path / "home_dpapi"
    home.mkdir()
    tok = ensure_local_api_token(home)
    assert len(tok) >= 16
    assert load_local_api_token(home) == tok
    enc = token_encoding(home)
    assert enc in ("dpapi", "plain")
    raw = token_path(home).read_text(encoding="utf-8")
    if enc == "dpapi":
        # Sealed: raw file must not contain the bearer string.
        assert tok not in raw
        outer = json.loads(raw)
        assert outer.get("v") == 2
        assert outer.get("dpapi")
    else:
        # Non-Windows or DPAPI failed — still ACL-hardened plaintext.
        assert tok in raw.strip() or raw.strip().startswith("{")
    if sys.platform == "win32":
        # On CI Windows DPAPI is available for the interactive user.
        assert enc == "dpapi"


def test_local_api_token_upgrades_legacy_plain(tmp_path, auth_on, monkeypatch):
    """Legacy plaintext token files remain readable and upgrade when DPAPI works."""
    import sys

    from remedy.interfaces.local_auth import load_local_api_token, token_encoding

    home = tmp_path / "home_legacy"
    home.mkdir()
    path = token_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    legacy = "legacy-plain-token-value-32chars!!"
    path.write_text(legacy + "\n", encoding="utf-8")
    assert load_local_api_token(home) == legacy
    # ensure re-seals when DPAPI available
    got = ensure_local_api_token(home)
    assert got == legacy
    if sys.platform == "win32":
        assert token_encoding(home) == "dpapi"
        assert legacy not in path.read_text(encoding="utf-8")
        assert load_local_api_token(home) == legacy


def test_self_improve_snapshot_shape(auth_on, tmp_path, monkeypatch):
    """HTTP /api/self-improve twin is gone; activity_snapshot still answers tools."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    from remedy.core.self_inject import activity_snapshot

    body = activity_snapshot(tmp_path)
    assert "enabled" in body
    assert "idle_s" in body
    assert "last_tick" in body


def test_gateway_serve_api_enables_auth(auth_on, tmp_path, monkeypatch):
    """``remedy gateway serve`` delegates to ``remedy serve`` (lock + auth)."""
    from types import SimpleNamespace

    from remedy.interfaces.cli import cmd_gateway as gateway_cli

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    db = tmp_path / "memory.db"
    db.write_bytes(b"")

    called: dict = {}

    def _fake_serve(args):
        called["args"] = args

    monkeypatch.setattr("remedy.interfaces.cli.cmd_runtime._cmd_serve", _fake_serve)
    gateway_cli._serve_api(db)  # noqa: SLF001
    ns = called.get("args")
    assert ns is not None
    assert getattr(ns, "skip_setup", False) is True
    assert getattr(ns, "host", "") == "127.0.0.1"
    assert int(getattr(ns, "port", 0)) == 7400
    # Real argparse namespace is also accepted (same defaults filled).
    called.clear()
    gateway_cli._serve_api(db, args=SimpleNamespace(home=str(tmp_path)))
    assert called.get("args") is not None


# Generic CI webhook (/api/webhook/{source}) is covered by Go httpapi/webhooks_test.go.
# Auth / CORS / bootstrap HTTP: native/go/httpapi/api_test.go + settings_test.go.
