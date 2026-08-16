"""Secure provider key store: no secrets in config.toml, DPAPI/plain round-trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.interfaces import secret_store
from remedy.interfaces.api_support import _write_config
from remedy.interfaces.config import (
    migrate_provider_keys,
    resolve_provider_api_key,
    set_provider_key,
)


def test_save_load_roundtrip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    secret_store.set_provider_secret("deepseek", "sk-test-deepseek", home=tmp_path)
    secret_store.set_provider_secret("openai", "sk-test-openai", home=tmp_path)

    keys = secret_store.load_provider_keys(tmp_path)
    assert keys["deepseek"] == "sk-test-deepseek"
    assert keys["openai"] == "sk-test-openai"

    path = secret_store.store_path(tmp_path)
    assert path.exists()
    raw = path.read_text(encoding="utf-8")
    # Raw file must not contain the plaintext key when DPAPI is used;
    # on plain fallback the key is in a restricted file — still never in config.
    outer = json.loads(raw)
    assert outer.get("encoding") in ("dpapi", "plain")
    if outer.get("encoding") == "dpapi":
        assert "sk-test-deepseek" not in raw
        assert "payload" in outer


def test_public_status_has_no_secrets(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    secret_store.set_provider_secret("deepseek", "sk-super-secret", home=tmp_path)
    status = secret_store.public_secret_status(tmp_path)
    blob = json.dumps(status)
    assert "sk-super-secret" not in blob
    assert status["provider_keys_set"]["deepseek"] is True
    assert "deepseek" in status["providers_with_keys"]
    # Fingerprints are opt-in (skip hash work on every Settings GET).
    assert "fingerprints" not in status
    assert status.get("encoding") in ("dpapi", "plain")
    if status.get("encoding") == "plain":
        assert "encoding_warning" in status
        assert "plaintext" in status["encoding_warning"].lower()
    else:
        assert "encoding_warning" not in status
    status_fp = secret_store.public_secret_status(tmp_path, include_fingerprints=True)
    assert status_fp["fingerprints"]["deepseek"]
    assert "sk-super-secret" not in json.dumps(status_fp)


def test_migrate_strips_config_plaintext(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    cfg = {
        "llm_provider": "xai",
        "llm_api_key": "sk-was-deepseek",
        "last_llm_provider": "deepseek",
        "provider_keys": {"openai": "sk-openai-legacy"},
        "home_dir": str(tmp_path),
    }
    cleaned = migrate_provider_keys(cfg)
    assert cleaned.get("llm_api_key") in ("", None)
    assert "provider_keys" not in cleaned or not cleaned.get("provider_keys")

    assert resolve_provider_api_key(cleaned, "deepseek", home=tmp_path) == "sk-was-deepseek"
    assert resolve_provider_api_key(cleaned, "openai", home=tmp_path) == "sk-openai-legacy"
    # xAI must not pick up the DeepSeek key
    xai_key = resolve_provider_api_key(cleaned, "xai", home=tmp_path)
    assert xai_key != "sk-was-deepseek"
    assert not (xai_key or "").startswith("sk-was")


def test_anthropic_key_saved_on_xai_type_lands_on_anthropic(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Pasting sk-ant-… while Type is still Grok must connect Anthropic."""
    from remedy.interfaces.config import (
        classify_provider_connection,
        get_provider_keys,
    )

    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    cfg = {"llm_provider": "xai", "home_dir": str(tmp_path)}
    set_provider_key(cfg, "xai", "sk-ant-api03-userpasted", home=tmp_path)
    keys = get_provider_keys(cfg, home=tmp_path)
    assert keys.get("anthropic") == "sk-ant-api03-userpasted"
    assert keys.get("xai") != "sk-ant-api03-userpasted"
    ok, reason = classify_provider_connection(
        "anthropic",
        cfg=cfg,
        keys=keys,
        keys_set={"anthropic": True},
        ollama_available=False,
        xai_connected=True,
    )
    assert ok is True
    assert reason in ("api_key", "resolved_key")


def test_rehome_misfiled_anthropic_key_under_xai(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    secret_store.set_provider_secret("xai", "sk-ant-api03-parked", home=tmp_path)
    from remedy.interfaces.config import get_provider_keys

    keys = get_provider_keys({"home_dir": str(tmp_path)}, home=tmp_path)
    assert keys.get("anthropic") == "sk-ant-api03-parked"
    assert "sk-ant" not in str(keys.get("xai") or "")


def test_write_config_never_persists_keys(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    cfg_path = tmp_path / "config.toml"
    cfg = {
        "llm_provider": "deepseek",
        "llm_model": "deepseek-chat",
        "llm_api_key": "sk-must-not-appear",
        "provider_keys": {"deepseek": "sk-must-not-appear"},
        "name": "Remedy",
    }
    set_provider_key(cfg, "deepseek", "sk-must-not-appear", home=tmp_path)
    _write_config(cfg_path, {**cfg, "llm_api_key": "sk-must-not-appear", "provider_keys": {"deepseek": "sk-must-not-appear"}})
    text = cfg_path.read_text(encoding="utf-8")
    assert "sk-must-not-appear" not in text
    assert "provider_keys" not in text
    assert resolve_provider_api_key({"llm_provider": "deepseek", "home_dir": str(tmp_path)}, "deepseek", home=tmp_path) == "sk-must-not-appear"


def test_clear_provider_secret(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    secret_store.set_provider_secret("groq", "gsk_test", home=tmp_path)
    secret_store.clear_provider_secret("groq", home=tmp_path)
    assert secret_store.get_provider_secret("groq", home=tmp_path) is None


def test_clear_all_provider_secrets_invalidates_cache(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Unlink-all must not leave secrets readable from the process cache."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    secret_store.set_provider_secret("openai", "sk-must-vanish", home=tmp_path)
    assert secret_store.load_provider_keys(tmp_path)["openai"] == "sk-must-vanish"
    secret_store.clear_provider_secret(None, home=tmp_path)
    assert secret_store.load_provider_keys(tmp_path) == {}
    assert secret_store.get_provider_secret("openai", home=tmp_path) is None


def test_provider_keys_cache_uses_mtime_ns(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    """Rapid rewrite with same second-resolution mtime still reloads."""
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    secret_store.set_provider_secret("a", "v1", home=tmp_path)
    assert secret_store.get_provider_secret("a", home=tmp_path) == "v1"
    secret_store.set_provider_secret("a", "v2", home=tmp_path)
    assert secret_store.get_provider_secret("a", home=tmp_path) == "v2"
