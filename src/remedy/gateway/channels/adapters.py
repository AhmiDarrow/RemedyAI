"""Backward-compatible re-exports of channel adapters."""

from __future__ import annotations

from remedy.gateway.channels.cli_channel import CLIChannel
from remedy.gateway.channels.discord import DiscordChannel
from remedy.gateway.channels.google_chat import GoogleChatChannel
from remedy.gateway.channels.matrix import MatrixChannel
from remedy.gateway.channels.mattermost import MattermostChannel
from remedy.gateway.channels.planned import PlannedMessengerChannel
from remedy.gateway.channels.signal_cli import SignalChannel
from remedy.gateway.channels.slack import SlackChannel
from remedy.gateway.channels.teams import TeamsChannel
from remedy.gateway.channels.telegram import TelegramChannel
from remedy.gateway.channels.web_channel import WebChannel
from remedy.gateway.channels.whatsapp import WhatsAppChannel

__all__ = [
    "CLIChannel",
    "DiscordChannel",
    "GoogleChatChannel",
    "MatrixChannel",
    "MattermostChannel",
    "PlannedMessengerChannel",
    "SignalChannel",
    "SlackChannel",
    "TeamsChannel",
    "TelegramChannel",
    "WebChannel",
    "WhatsAppChannel",
]
