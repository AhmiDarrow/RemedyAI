"""Plan completion tests: providers catalog, CLI auth, ollama detect, adapters."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from remedy.core.providers import get_provider, get_provider_for_base_url
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    detect_ollama,
    public_provider_catalog,
)
from remedy.interfaces.cli import build_parser, _cmd_auth


class TestCatalogSprintC:
    def test_known_brands_present(self):
        for pid in ("xai", "groq", "mistral", "openai", "anthropic", "ollama", "custom"):
            assert pid in PROVIDER_CATALOG

    def test_xai_no_base_url_in_public_meta(self):
        xai = next(p for p in public_provider_catalog() if p["id"] == "xai")
        assert xai["show_base_url"] is False
        assert xai["oauth"] is True

    def test_custom_is_advanced(self):
        custom = next(p for p in public_provider_catalog() if p["id"] == "custom")
        assert custom["advanced"] is True

    def test_adapters_registered(self):
        assert get_provider("xai").provider_name == "xai"
        assert get_provider("groq").provider_name == "groq"
        assert get_provider("mistral").provider_name == "mistral"
        assert get_provider_for_base_url("https://api.groq.com/openai/v1").provider_name == "groq"
        assert get_provider_for_base_url("https://api.mistral.ai/v1").provider_name == "mistral"


class TestOllamaDetect:
    def test_detect_unavailable_is_safe(self):
        # Force network failure with bogus host
        result = detect_ollama(base_url="http://127.0.0.1:9/v1", timeout=0.2)
        assert result["available"] is False
        assert result["models"] == []

    def test_detect_success_mocked(self):
        import io
        import json

        payload = json.dumps({"models": [{"name": "llama3.2:latest"}, {"name": "qwen2.5"}]}).encode()

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return payload

        with patch("urllib.request.urlopen", return_value=FakeResp()):
            result = detect_ollama()
        assert result["available"] is True
        assert "llama3.2" in result["models"]
        assert "qwen2.5" in result["models"]


class TestCliAuth:
    def test_parser_auth_subcommands(self):
        p = build_parser()
        for argv in (
            ["auth", "status", "xai"],
            ["auth", "logout", "xai"],
            ["auth", "login", "xai"],
            ["auth", "apikey", "xai", "xai-test"],
        ):
            ns = p.parse_args(argv)
            assert ns.command == "auth"

    def test_apikey_and_status(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys):
        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        from argparse import Namespace

        args = Namespace(
            auth_cmd="apikey",
            provider="xai",
            api_key="xai-cli-key",
            home=str(tmp_path),
        )
        _cmd_auth(args)
        out = capsys.readouterr().out
        assert "Saved" in out or "connected" in out.lower()

        args2 = Namespace(auth_cmd="status", provider="xai", home=str(tmp_path))
        _cmd_auth(args2)
        out2 = capsys.readouterr().out
        assert "xai" in out2.lower() or "api_key" in out2.lower() or "connected" in out2.lower()

        args3 = Namespace(auth_cmd="logout", provider="xai", home=str(tmp_path))
        _cmd_auth(args3)
        from remedy.interfaces import xai_auth

        assert xai_auth.load_credentials(home=tmp_path).connected is False


class TestAuthRoutes:
    def test_routes_register(self):
        from fastapi import FastAPI

        from remedy.interfaces.routes.auth import register_auth_routes

        app = FastAPI()
        register_auth_routes(app)
        paths = {getattr(r, "path", None) for r in app.routes}
        assert "/api/providers" in paths
        assert "/api/providers/ollama/detect" in paths
        assert "/api/auth/xai/login" in paths
        assert "/api/auth/xai" in paths
