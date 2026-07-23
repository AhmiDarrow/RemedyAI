"""Tests for xAI dual auth (API key + OAuth device-code store)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from remedy.interfaces import xai_auth
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    config_to_agent_config,
    infer_provider_from_base_url,
    infer_provider_from_model,
    normalize_llm_settings,
    provider_credentials_ready,
)


class TestXaiCatalog:
    def test_xai_in_catalog(self):
        assert "xai" in PROVIDER_CATALOG
        assert PROVIDER_CATALOG["xai"]["base_url"] == "https://api.x.ai/v1"
        assert "oauth" in PROVIDER_CATALOG["xai"]["auth"]
        models = {m["id"] for m in PROVIDER_CATALOG["xai"]["models"]}
        assert "grok-3-mini" in models

    def test_infer_model_and_url(self):
        assert infer_provider_from_model("grok-3") == "xai"
        assert infer_provider_from_model("grok-4") == "xai"
        assert infer_provider_from_base_url("https://api.x.ai/v1") == "xai"

    def test_normalize_snaps_to_xai(self):
        prov, model, url = normalize_llm_settings("xai", "gpt-4o", "https://api.openai.com/v1")
        assert prov == "xai"
        assert model.startswith("grok")
        assert "x.ai" in url


class TestXaiCredentialsStore:
    def test_save_and_load_api_key(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("REMEDY_XAI_API_KEY", raising=False)

        creds = xai_auth.save_api_key("xai-test-key", home=tmp_path)
        assert creds.auth_method == "api_key"
        assert creds.connected
        assert creds.bearer_token() == "xai-test-key"

        loaded = xai_auth.load_credentials(home=tmp_path)
        assert loaded.api_key == "xai-test-key"
        assert loaded.to_public_dict()["connected"] is True
        assert loaded.to_public_dict()["has_api_key"] is True

        path = xai_auth.auth_path(home=tmp_path)
        assert path.exists()
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["auth_method"] == "api_key"

    def test_oauth_store_and_resolve(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("REMEDY_XAI_API_KEY", raising=False)

        creds = xai_auth.XaiCredentials(
            auth_method="oauth",
            access_token="access-abc",
            refresh_token="refresh-xyz",
            expires_at=time.time() + 3600,
        )
        xai_auth.save_credentials(creds, home=tmp_path)
        assert xai_auth.resolve_bearer(home=tmp_path) == "access-abc"

    def test_clear_credentials(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        xai_auth.save_api_key("xai-tmp", home=tmp_path)
        xai_auth.clear_credentials(home=tmp_path)
        assert not xai_auth.auth_path(home=tmp_path).exists()
        assert xai_auth.load_credentials(home=tmp_path).connected is False

    def test_env_bootstrap(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
        # No file → env
        creds = xai_auth.load_credentials(home=tmp_path)
        assert creds.api_key == "xai-from-env"
        assert creds.auth_method == "api_key"


class TestXaiCredentialsReady:
    def test_ready_via_store(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("REMEDY_XAI_API_KEY", raising=False)
        monkeypatch.delenv("REMEDY_LLM_API_KEY", raising=False)

        assert provider_credentials_ready(
            {"llm_provider": "xai", "llm_base_url": "https://api.x.ai/v1", "home_dir": str(tmp_path)}
        ) is False

        xai_auth.save_api_key("xai-ready", home=tmp_path)
        assert provider_credentials_ready(
            {"llm_provider": "xai", "llm_base_url": "https://api.x.ai/v1", "home_dir": str(tmp_path)}
        ) is True

    def test_config_to_agent_uses_bearer(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        monkeypatch.delenv("REMEDY_LLM_API_KEY", raising=False)
        monkeypatch.delenv("XAI_API_KEY", raising=False)

        xai_auth.save_api_key("xai-agent-key", home=tmp_path)
        agent = config_to_agent_config(
            {
                "llm_provider": "xai",
                "llm_model": "grok-3-mini",
                "llm_base_url": "https://api.x.ai/v1",
                "home_dir": str(tmp_path),
            }
        )
        assert agent.llm_api_key == "xai-agent-key"
        assert agent.llm_provider == "xai"


class TestEnvBootstrap:
    def test_xai_api_key_env_preselects_xai(self, monkeypatch: pytest.MonkeyPatch):
        from remedy.interfaces.config import apply_env_provider_bootstrap

        monkeypatch.setenv("XAI_API_KEY", "xai-bootstrap")
        monkeypatch.delenv("REMEDY_LLM_API_KEY", raising=False)
        out = apply_env_provider_bootstrap(
            {"llm_provider": "openai", "llm_model": "gpt-4o-mini", "llm_api_key": ""}
        )
        assert out["llm_provider"] == "xai"
        assert "x.ai" in out["llm_base_url"]
        assert out["llm_model"].startswith("grok")

    def test_no_switch_when_key_already_set(self, monkeypatch: pytest.MonkeyPatch):
        from remedy.interfaces.config import apply_env_provider_bootstrap

        monkeypatch.setenv("XAI_API_KEY", "xai-bootstrap")
        out = apply_env_provider_bootstrap(
            {
                "llm_provider": "openai",
                "llm_api_key": "sk-already",
                "llm_model": "gpt-4o-mini",
            }
        )
        assert out["llm_provider"] == "openai"

    def test_public_catalog_marks_custom_advanced(self):
        from remedy.interfaces.config import public_provider_catalog

        items = {p["id"]: p for p in public_provider_catalog()}
        assert items["xai"]["oauth"] is True
        assert items["xai"]["show_base_url"] is False
        assert items["custom"]["advanced"] is True
        assert items["custom"]["show_base_url"] is True
        assert "groq" in items
        assert "mistral" in items


class TestXaiDeviceLoginMock:
    def test_start_device_login_parses_response(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))

        fake = {
            "device_code": "dev-1",
            "user_code": "ABCD-EFGH",
            "verification_uri": "https://accounts.x.ai/oauth2/device",
            "interval": 5,
            "expires_in": 600,
        }

        with patch.object(xai_auth, "_http_form", return_value=fake):
            # Avoid background poll doing real HTTP
            with patch.object(xai_auth.threading, "Thread") as thr:
                thr.return_value.start = lambda: None
                result = xai_auth.start_device_login(home=tmp_path)

        assert result["user_code"] == "ABCD-EFGH"
        assert result["session_id"] == "ABCD-EFGH"
        assert "verification_uri" in result
        assert result["status"] == "pending"

    def test_provider_adapter_registered(self):
        from remedy.core.providers import get_provider, get_provider_for_base_url

        p = get_provider("xai")
        assert p.provider_name == "xai"
        assert "x.ai" in p.default_base_url
        assert get_provider_for_base_url("https://api.x.ai/v1").provider_name == "xai"
