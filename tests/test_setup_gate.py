"""Tests for first-run setup gate (before launch / skip / remember)."""

from __future__ import annotations

from pathlib import Path

from remedy.interfaces.config import (
    mark_setup_completed,
    needs_first_run_setup,
    provider_credentials_ready,
)


class TestNeedsFirstRunSetup:
    def test_no_config_file(self, tmp_path: Path):
        missing = tmp_path / "config.toml"
        assert needs_first_run_setup({}, config_path=missing) is True

    def test_setup_completed_true(self, tmp_path: Path):
        path = tmp_path / "config.toml"
        path.write_text('setup_completed = true\n', encoding="utf-8")
        assert needs_first_run_setup(config_path=path) is False

    def test_setup_completed_false(self, tmp_path: Path):
        path = tmp_path / "config.toml"
        path.write_text('setup_completed = false\nname = "Remedy"\n', encoding="utf-8")
        assert needs_first_run_setup(config_path=path) is True

    def test_legacy_config_without_flag(self, tmp_path: Path):
        """Existing installs without the flag must not be forced through the wizard."""
        path = tmp_path / "config.toml"
        path.write_text('name = "Remedy"\nllm_provider = "openai"\n', encoding="utf-8")
        assert needs_first_run_setup(config_path=path) is False

    def test_in_memory_flag_overrides_file(self, tmp_path: Path):
        path = tmp_path / "config.toml"
        path.write_text('setup_completed = true\n', encoding="utf-8")
        assert needs_first_run_setup({"setup_completed": False}, config_path=path) is True


class TestMarkSetupCompleted:
    def test_creates_config_when_missing(self, tmp_path: Path):
        path = tmp_path / "config.toml"
        out = mark_setup_completed(config_path=path)
        assert out == path
        text = path.read_text(encoding="utf-8")
        assert "setup_completed = true" in text

    def test_preserves_existing_keys(self, tmp_path: Path):
        path = tmp_path / "config.toml"
        path.write_text(
            'name = "Remedy"\nllm_provider = "deepseek"\nsetup_completed = false\n',
            encoding="utf-8",
        )
        mark_setup_completed(config_path=path, extra={"persona": "efficient"})
        text = path.read_text(encoding="utf-8")
        assert "setup_completed = true" in text
        assert "deepseek" in text
        assert "efficient" in text
        assert needs_first_run_setup(config_path=path) is False


class TestProviderCredentialsReady:
    def test_api_key_present(self):
        assert provider_credentials_ready(
            {"llm_provider": "openai", "llm_api_key": "sk-test"}
        ) is True

    def test_local_url(self):
        assert provider_credentials_ready(
            {"llm_base_url": "http://127.0.0.1:11434/v1", "llm_api_key": ""}
        ) is True

    def test_ollama_provider(self):
        assert provider_credentials_ready({"llm_provider": "ollama"}) is True

    def test_missing(self):
        assert provider_credentials_ready(
            {"llm_provider": "openai", "llm_base_url": "https://api.openai.com/v1"}
        ) is False

    def test_xai_without_credentials(self, monkeypatch):
        # Isolate from any on-disk OAuth session on the developer machine.
        monkeypatch.setattr(
            "remedy.interfaces.xai_auth.resolve_bearer",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "remedy.interfaces.secret_store.get_provider_secret",
            lambda *a, **k: None,
        )
        assert provider_credentials_ready(
            {
                "llm_provider": "xai",
                "llm_base_url": "https://api.x.ai/v1",
                "llm_api_key": "",
            }
        ) is False
