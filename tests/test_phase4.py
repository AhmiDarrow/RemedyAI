"""Phase 4 leftovers: ChannelKind + API models (FastAPI create_app retired)."""

from __future__ import annotations

from remedy.models import ChannelKind


class TestChannelKinds:
    def test_channel_kind_values(self):
        assert ChannelKind.CLI.value == "cli"
        assert ChannelKind.TELEGRAM.value == "telegram"
        assert ChannelKind.DISCORD.value == "discord"
        assert ChannelKind.SLACK.value == "slack"
        assert ChannelKind.WEB.value == "web"
        assert ChannelKind.API.value == "api"


class TestAPI:
    def test_status_model(self):
        from remedy.interfaces.api_models import StatusResponse

        assert StatusResponse is not None

    def test_fastapi_api_module_gone(self):
        import importlib.util
        from pathlib import Path

        assert importlib.util.find_spec("remedy.interfaces.api") is None
        assert not Path("src/remedy/interfaces/api.py").exists()
