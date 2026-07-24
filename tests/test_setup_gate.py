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

    def test_scalars_written_before_tables(self, tmp_path: Path):
        """Root keys after [table] sections corrupt TOML (duplicate key errors)."""
        path = tmp_path / "config.toml"
        path.write_text(
            'name = "Remedy"\nsetup_completed = false\n\n[slack]\nbot_token = ""\n',
            encoding="utf-8",
        )
        mark_setup_completed(
            config_path=path,
            extra={
                "launch_at_login": False,
                "secrets_store": "auth/provider_keys.json",
                "slack": {"bot_token": "", "channel_id": ""},
            },
        )
        text = path.read_text(encoding="utf-8")
        # setup_completed / launch_at_login must appear before any [section]
        first_table = text.find("[")
        assert first_table > 0
        head = text[:first_table]
        assert "setup_completed = true" in head
        assert "launch_at_login = false" in head
        assert "secrets_store" in head
        # File must still parse
        assert needs_first_run_setup(config_path=path) is False


class TestCorruptConfigNeedsSetup:
    def test_duplicate_key_toml_forces_wizard(self, tmp_path: Path):
        path = tmp_path / "config.toml"
        # Classic bug: root key written twice under last table
        path.write_text(
            '[slack]\nbot_token = ""\nsecrets_store = "a"\nsecrets_store = "b"\n',
            encoding="utf-8",
        )
        assert needs_first_run_setup(config_path=path) is True


class TestApiWriteConfigOrder:
    def test_api_write_config_scalars_before_tables(self, tmp_path: Path):
        """Settings PUT path must never emit root keys after [table] sections."""
        import tomllib

        from remedy.interfaces.api_support import _write_config

        path = tmp_path / "config.toml"
        _write_config(
            path,
            {
                "name": "Remedy",
                "setup_completed": True,
                "secrets_store": "auth/provider_keys.json",
                "launch_at_login": False,
                "gateway": {"heartbeat_interval": 60, "rate_limit": 120},
                "slack": {"bot_token": "", "channel_id": ""},
                "llm_api_key": "sk-should-not-appear",
                "provider_keys": {"openai": "sk-nope"},
            },
        )
        text = path.read_text(encoding="utf-8")
        first_table = text.find("[")
        assert first_table > 0
        head = text[:first_table]
        assert "setup_completed = true" in head
        assert "secrets_store" in head
        assert "launch_at_login = false" in head
        assert "sk-should-not-appear" not in text
        assert "sk-nope" not in text
        # Round-trip must parse
        data = tomllib.loads(text)
        assert data["setup_completed"] is True
        assert data["secrets_store"] == "auth/provider_keys.json"
        assert isinstance(data.get("slack"), dict)

        # Multiple update cycles (scalars added after nested load) stay valid
        for i in range(3):
            data["close_to_tray"] = False
            data["start_in_tray"] = False
            data["secrets_store"] = "auth/provider_keys.json"
            _write_config(path, data)
            data = tomllib.loads(path.read_text(encoding="utf-8"))
        assert data["close_to_tray"] is False
        assert "slack" in data


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
