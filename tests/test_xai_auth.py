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


class TestXaiOAuthHosts:
    """Device/token API must hit auth.x.ai — accounts.x.ai 307s to /sign-in."""

    def test_device_and_token_urls_use_auth_host(self):
        assert xai_auth.OAUTH_SERVER == "https://auth.x.ai"
        assert xai_auth.DEVICE_CODE_URL.startswith("https://auth.x.ai/")
        assert xai_auth.TOKEN_URL.startswith("https://auth.x.ai/")
        assert "accounts.x.ai" not in xai_auth.DEVICE_CODE_URL
        assert "accounts.x.ai" not in xai_auth.TOKEN_URL
        for url in xai_auth._DEVICE_CODE_CANDIDATES:
            assert "auth.x.ai" in url
            assert "accounts.x.ai" not in url

    def test_http_form_refuses_accounts_device_api(self):
        with pytest.raises(RuntimeError) as ei:
            xai_auth._http_form(
                "https://accounts.x.ai/oauth2/device/code",
                {"client_id": "test"},
            )
        assert "refusing accounts.x.ai" in str(ei.value)


class TestXaiCatalog:
    def test_xai_in_catalog(self):
        assert "xai" in PROVIDER_CATALOG
        assert PROVIDER_CATALOG["xai"]["base_url"] == "https://api.x.ai/v1"
        assert "oauth" in PROVIDER_CATALOG["xai"]["auth"]
        models = {m["id"] for m in PROVIDER_CATALOG["xai"]["models"]}
        assert models & {"grok-4.5", "grok-4.3", "grok-4"}

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
        # Plain JSON or DPAPI envelope (v2)
        if data.get("v") == 2 and data.get("dpapi"):
            assert isinstance(data["dpapi"], str) and len(data["dpapi"]) > 8
        else:
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

    def test_credentials_encoding_public(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        """Public status surfaces encoding; plaintext warns when connected."""
        import sys

        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("REMEDY_XAI_API_KEY", raising=False)
        xai_auth.save_api_key("xai-enc-test-key", home=tmp_path)
        enc = xai_auth.credentials_encoding(home=tmp_path)
        assert enc in ("dpapi", "plain")
        pub = xai_auth.load_credentials(home=tmp_path).to_public_dict(home=tmp_path)
        assert pub["credentials_encoding"] == enc
        assert "xai-enc-test-key" not in json.dumps(pub)
        if enc == "plain":
            assert "credentials_encoding_warning" in pub
        else:
            assert "credentials_encoding_warning" not in pub
        if sys.platform == "win32":
            assert enc == "dpapi"

    def test_env_bootstrap(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        monkeypatch.setenv("XAI_API_KEY", "xai-from-env")
        # No file → env
        creds = xai_auth.load_credentials(home=tmp_path)
        assert creds.api_key == "xai-from-env"
        assert creds.auth_method == "api_key"



    def test_corrupt_expires_at_does_not_raise(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Malformed expires_at in on-disk store must not break connected/bearer."""
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("REMEDY_XAI_API_KEY", raising=False)
        path = xai_auth.auth_path(home=tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "auth_method": "oauth",
                    "access_token": "access-bad-exp",
                    "refresh_token": "refresh-ok",
                    "expires_at": "not-a-timestamp",
                    "token_type": "Bearer",
                }
            )
            + "\n",
            encoding="utf-8",
        )
        loaded = xai_auth.load_credentials(home=tmp_path)
        assert loaded.expires_at is None
        assert loaded.connected is True  # token present, no usable expiry
        assert loaded.bearer_token() == "access-bad-exp"
        pub = loaded.to_public_dict()
        assert pub["connected"] is True
        assert pub["has_oauth"] is True

    def test_save_leaves_no_tmp_and_roundtrips(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Atomic write must replace the final path and clean up .tmp."""
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        monkeypatch.delenv("XAI_API_KEY", raising=False)
        monkeypatch.delenv("REMEDY_XAI_API_KEY", raising=False)
        xai_auth.save_api_key("xai-atomic", home=tmp_path)
        path = xai_auth.auth_path(home=tmp_path)
        assert path.exists()
        assert [f.name for f in path.parent.iterdir()] == [path.name], (
            "a scratch file was left behind"
        )
        assert xai_auth.load_credentials(home=tmp_path).api_key == "xai-atomic"

    def test_save_uses_a_unique_scratch_name(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """``path.with_suffix(path.suffix + ".tmp")`` gave every writer the
        same scratch file; two saves at once published interleaved bytes."""
        import os

        seen: list[str] = []
        real_replace = os.replace

        def _spy(src, dst):
            seen.append(Path(src).name)
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", _spy)
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        xai_auth.save_api_key("xai-unique", home=tmp_path)
        path = xai_auth.auth_path(home=tmp_path)
        assert seen
        assert seen[-1] != path.name + ".tmp"
        assert str(os.getpid()) in seen[-1]

    def test_clear_removes_a_stale_unique_scratch_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        path = xai_auth.auth_path(home=tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        stale = path.with_name(f".{path.name}.12345.678.tmp")
        stale.write_text("{partial", encoding="utf-8")
        xai_auth.clear_credentials(home=tmp_path)
        assert not stale.exists()

    def test_clear_also_removes_stale_tmp(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        path = xai_auth.auth_path(home=tmp_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('{"auth_method":"api_key","api_key":"x"}\n', encoding="utf-8")
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text("{partial", encoding="utf-8")
        xai_auth.clear_credentials(home=tmp_path)
        assert not path.exists()
        assert not tmp.exists()


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

    def test_public_catalog_marks_custom_primary(self):
        from remedy.interfaces.config import public_provider_catalog

        items = {p["id"]: p for p in public_provider_catalog()}
        assert items["xai"]["oauth"] is True
        assert items["xai"]["show_base_url"] is False
        assert items["custom"]["advanced"] is False
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


class TestSettingsIncludesXaiAuth:
    def test_get_settings_includes_xai_auth_when_provider_is_not_xai(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        home = tmp_path.as_posix().replace("\\", "/")
        (tmp_path / "config.toml").write_text(
            f'name = "Remedy"\nsetup_completed = true\nllm_provider = "demo"\nhome_dir = "{home}"\n',
            encoding="utf-8",
        )
        xai_auth.save_api_key("xai-visible", home=tmp_path)
        from fastapi.testclient import TestClient

        from remedy.interfaces.api import create_app

        client = TestClient(create_app())
        r = client.get("/api/settings")
        assert r.status_code == 200, r.text
        data = r.json()
        assert data.get("llm_provider") in ("demo", "xai") or "xai_auth" in data
        assert "xai_auth" in data
        assert data["xai_auth"].get("connected") is True
