"""Plan completion tests: providers catalog, CLI auth, ollama detect, adapters."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from remedy.core.providers import get_provider, get_provider_for_base_url
from remedy.interfaces.cli import _cmd_auth, build_parser
from remedy.interfaces.config import (
    PROVIDER_CATALOG,
    detect_ollama,
    public_provider_catalog,
)


class TestCatalogSprintC:
    def test_known_brands_present(self):
        for pid in ("xai", "groq", "mistral", "openai", "anthropic", "ollama", "custom"):
            assert pid in PROVIDER_CATALOG

    def test_xai_no_base_url_in_public_meta(self):
        xai = next(p for p in public_provider_catalog() if p["id"] == "xai")
        assert xai["show_base_url"] is False
        assert xai["oauth"] is True

    def test_custom_is_primary_option(self):
        custom = next(p for p in public_provider_catalog() if p["id"] == "custom")
        assert custom["advanced"] is False
        assert custom["show_base_url"] is True

    def test_custom_name_from_config(self):
        items = public_provider_catalog({"custom_llm_name": "  LM Studio  "})
        custom = next(p for p in items if p["id"] == "custom")
        assert custom["name"] == "LM Studio"

    def test_custom_name_default_when_unset(self):
        items = public_provider_catalog({"custom_llm_name": ""})
        custom = next(p for p in items if p["id"] == "custom")
        assert custom["name"] == "Custom / OpenAI-compatible"


class TestCustomEndpointRoundTrip:
    """PUT /api/settings stores custom_llm_name; GET + providers reflect it."""

    def test_custom_name_round_trip(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from fastapi.testclient import TestClient

        from remedy.interfaces.api import create_app

        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        client = TestClient(create_app())

        r = client.put(
            "/api/settings",
            json={
                "llm_provider": "custom",
                "llm_base_url": "http://127.0.0.1:5001/api/v1",
                "custom_llm_name": "  LM Studio local  ",
                "setup_completed": True,
            },
        )
        assert r.status_code == 200, r.text
        assert r.json()["custom_llm_name"] == "LM Studio local"

        r = client.get("/api/settings")
        assert r.status_code == 200, r.text
        assert r.json()["custom_llm_name"] == "LM Studio local"

        r = client.get("/api/providers")
        assert r.status_code == 200, r.text
        custom = next(p for p in r.json()["providers"] if p["id"] == "custom")
        assert custom["name"] == "LM Studio local"
        assert custom["advanced"] is False

    def test_custom_name_cleared_to_default(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        from fastapi.testclient import TestClient

        from remedy.interfaces.api import create_app

        monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
        client = TestClient(create_app())

        r = client.put(
            "/api/settings",
            json={
                "llm_provider": "custom",
                "custom_llm_name": "Temp Name",
                "setup_completed": True,
            },
        )
        assert r.status_code == 200, r.text
        r = client.put("/api/settings", json={"custom_llm_name": ""})
        assert r.status_code == 200, r.text
        assert r.json()["custom_llm_name"] == ""

        r = client.get("/api/providers")
        custom = next(p for p in r.json()["providers"] if p["id"] == "custom")
        assert custom["name"] == "Custom / OpenAI-compatible"

    def test_adapters_registered(self):
        assert get_provider("xai").provider_name == "xai"
        assert get_provider("groq").provider_name == "groq"
        assert get_provider("mistral").provider_name == "mistral"
        assert get_provider_for_base_url("https://api.groq.com/openai/v1").provider_name == "groq"
        assert get_provider_for_base_url("https://api.mistral.ai/v1").provider_name == "mistral"


class TestOllamaDetect:
    def test_detect_unavailable_is_safe(self):
        from remedy.interfaces.model_discovery import invalidate_ollama_detect_cache

        invalidate_ollama_detect_cache()
        # Force network failure with bogus host
        result = detect_ollama(base_url="http://127.0.0.1:9/v1", timeout=0.2)
        assert result["available"] is False
        assert result["models"] == []

    def test_detect_success_mocked(self):
        import json

        from remedy.interfaces import model_discovery as md

        md.invalidate_ollama_detect_cache()
        payload = json.dumps({"models": [{"name": "llama3.2:latest"}, {"name": "qwen2.5"}]}).encode()

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return payload

        with patch.object(md, "_PRECHECK_LOCAL_LISTEN", False):
            with patch("urllib.request.urlopen", return_value=FakeResp()):
                result = detect_ollama()
        assert result["available"] is True
        assert "llama3.2" in result["models"]
        assert "qwen2.5" in result["models"]

    def test_closed_local_port_skips_http_and_is_fast(self):
        import time

        from remedy.interfaces import model_discovery as md

        md.invalidate_ollama_detect_cache()
        with patch("urllib.request.urlopen") as uo:
            t0 = time.perf_counter()
            result = detect_ollama(base_url="http://127.0.0.1:9/v1", timeout=1.5)
            ms = (time.perf_counter() - t0) * 1000
        assert result["available"] is False
        uo.assert_not_called()
        assert ms < 500, f"closed-port detect took {ms:.0f}ms (wanted fail-fast, not 1.5s timeout)"

    def test_detect_cache_hit_and_force(self):
        import json

        from remedy.interfaces import model_discovery as md

        md.invalidate_ollama_detect_cache()
        payload = json.dumps({"models": [{"name": "llama3.2:latest"}]}).encode()

        class FakeResp:
            def __enter__(self):
                return self

            def __exit__(self, *a):
                return False

            def read(self):
                return payload

        with patch.object(md, "_PRECHECK_LOCAL_LISTEN", False):
            with patch("urllib.request.urlopen", return_value=FakeResp()) as uo:
                a = detect_ollama(base_url="http://127.0.0.1:11434/v1")
                b = detect_ollama(base_url="http://127.0.0.1:11434/v1")
                assert a["available"] is True and b["available"] is True
                assert a["models"] == b["models"] == ["llama3.2"]
                assert uo.call_count == 1
                c = detect_ollama(base_url="http://127.0.0.1:11434/v1", force=True)
                assert c["available"] is True
                assert uo.call_count == 2


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
