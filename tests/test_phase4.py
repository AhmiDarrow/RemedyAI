"""Phase 4 leftovers: ChannelKind + FastAPI create_app deleted."""

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
    def test_fastapi_api_module_gone(self):
        import importlib.util
        from pathlib import Path

        assert importlib.util.find_spec("remedy.interfaces.api") is None
        assert not Path("src/remedy/interfaces/api.py").exists()

    def test_fastapi_api_models_gone(self):
        import importlib.util
        from pathlib import Path

        assert importlib.util.find_spec("remedy.interfaces.api_models") is None
        assert not Path("src/remedy/interfaces/api_models.py").exists()

    def test_session_event_hub_twin_gone(self):
        import importlib.util
        from pathlib import Path

        assert importlib.util.find_spec("remedy.interfaces.session_events") is None
        assert not Path("src/remedy/interfaces/session_events.py").exists()
        assert importlib.util.find_spec("remedy.interfaces.session_public") is None
        assert not Path("src/remedy/interfaces/session_public.py").exists()
