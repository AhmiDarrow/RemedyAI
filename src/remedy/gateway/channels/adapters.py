"""Backward-compatible re-exports of channel adapters.

Prefer importing from platform modules directly:
  remedy.gateway.channels.telegram.TelegramChannel
This package root keeps existing ``from ...adapters import X`` working.
"""

from __future__ import annotations

from remedy.gateway.channels.cli_channel import CLIChannel
from remedy.gateway.channels.discord import DiscordChannel
from remedy.gateway.channels.mattermost import MattermostChannel
from remedy.gateway.channels.planned import PlannedMessengerChannel
from remedy.gateway.channels.slack import SlackChannel
from remedy.gateway.channels.telegram import TelegramChannel
from remedy.gateway.channels.web_channel import WebChannel

__all__ = [
    "CLIChannel",
    "DiscordChannel",
    "MattermostChannel",
    "PlannedMessengerChannel",
    "SlackChannel",
    "TelegramChannel",
    "WebChannel",
]
