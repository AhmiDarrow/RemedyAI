"""Phase 4 leftovers: ChannelKind + API model re-exports (gateway twins gone)."""

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
    def test_status_model(self):
        from remedy.interfaces.api import StatusResponse

        assert StatusResponse is not None

    def test_create_app_harness_gone(self):
        import remedy.interfaces.api as api

        assert not hasattr(api, "create_app")
