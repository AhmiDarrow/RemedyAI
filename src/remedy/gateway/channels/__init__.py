"""Gateway channel adapters (one module per platform)."""

from remedy.gateway.channels.adapters import (
    CLIChannel,
    DiscordChannel,
    MattermostChannel,
    PlannedMessengerChannel,
    SlackChannel,
    TelegramChannel,
    WebChannel,
)

__all__ = [
    "CLIChannel",
    "DiscordChannel",
    "MattermostChannel",
    "PlannedMessengerChannel",
    "SlackChannel",
    "TelegramChannel",
    "WebChannel",
]
