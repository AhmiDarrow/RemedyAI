"""Secure provider key store: no secrets in config.toml, DPAPI/plain round-trip."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.interfaces import secret_store
from remedy.interfaces.config import (
    migrate_provider_keys,
    resolve_provider_api_key,
    set_provider_key,
)
from remedy.interfaces.api_support import _write_config


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
    assert status["fingerprints"]["deepseek"]


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
