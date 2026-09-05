"""Phase 4 leftovers: ChannelKind + TestClient create_app (gateway twins gone)."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from remedy.models import AgentConfig, ChannelKind


@pytest.fixture
def runtime():
    rt = MagicMock()
    rt.config = AgentConfig(home_dir="~/.remedy")
    rt.memory = MagicMock()
    rt.skills = MagicMock()
    rt.handoff = MagicMock()
    return rt


class TestChannelKinds:
    def test_channel_kind_values(self):
        assert ChannelKind.CLI.value == "cli"
        assert ChannelKind.TELEGRAM.value == "telegram"
        assert ChannelKind.DISCORD.value == "discord"
        assert ChannelKind.SLACK.value == "slack"
        assert ChannelKind.WEB.value == "web"
        assert ChannelKind.API.value == "api"


class TestAPI:
    def test_create_app_no_deps(self):
        from remedy.interfaces.api import create_app

        app = create_app(title="Test", version="0.1.0")
        assert app.title == "Test"

    def test_create_app_with_runtime(self, runtime):
        from remedy.interfaces.api import create_app

        app = create_app(runtime=runtime, gateway=None, memory=None)
        assert app.title == "Remedy AI"

    def test_status_model(self):
        from remedy.interfaces.api import StatusResponse

        assert StatusResponse is not None
