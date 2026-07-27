"""Register enabled messenger adapters on a Gateway from config."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from remedy.gateway.channels import (
    DiscordChannel,
    MattermostChannel,
    PlannedMessengerChannel,
    SlackChannel,
    TelegramChannel,
)
from remedy.gateway.messengers import (
    get_messenger,
    parse_list_field,
    resolve_channel_secret,
)
from remedy.gateway.router import Gateway
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


def _section(cfg: dict[str, Any], channel: str) -> dict[str, Any]:
    raw = cfg.get(channel)
    return dict(raw) if isinstance(raw, dict) else {}


def _home_from_cfg(cfg: dict[str, Any]) -> Path | None:
    home = cfg.get("home_dir")
    if home:
        return Path(str(home)).expanduser()
    return None


def register_messenger_channels(
    gw: Gateway,
    cfg: dict[str, Any],
    *,
    token_telegram: str = "",
    token_discord: str = "",
    token_slack: str = "",
) -> list[str]:
    """Register enabled messenger adapters from config (+ CLI token overrides)."""
    home = _home_from_cfg(cfg)
    enabled_raw = cfg.get("enabled_channels") or []
    if not isinstance(enabled_raw, list):
        enabled_raw = [enabled_raw] if enabled_raw else []
    enabled = {str(x).strip().lower() for x in enabled_raw if str(x).strip()}

    if token_telegram:
        enabled.add("telegram")
    if token_discord:
        enabled.add("discord")
    if token_slack:
        enabled.add("slack")

    registered: list[str] = []

    if "telegram" in enabled:
        sec = _section(cfg, "telegram")
        token = token_telegram or resolve_channel_secret(
            cfg, "telegram", "bot_token", home=home
        )
        chat_ids = parse_list_field(sec.get("allow_chat_ids") or sec.get("chat_ids"))
        allow_all = bool(sec.get("allow_all"))
        if token:
            gw.register_channel(
                TelegramChannel(
                    gw,
                    bot_token=token,
                    chat_ids=chat_ids,
                    allow_all=allow_all,
                )
            )
            registered.append("telegram")
        else:
            logger.warning("telegram enabled but no bot_token")

    if "discord" in enabled:
        sec = _section(cfg, "discord")
        token = token_discord or resolve_channel_secret(
            cfg, "discord", "bot_token", home=home
        )
        if token:
            gw.register_channel(
                DiscordChannel(
                    gw,
                    bot_token=token,
                    channel_id=str(sec.get("channel_id") or ""),
                )
            )
            registered.append("discord")

    if "slack" in enabled:
        sec = _section(cfg, "slack")
        token = token_slack or resolve_channel_secret(
            cfg, "slack", "bot_token", home=home
        )
        if token:
            gw.register_channel(
                SlackChannel(
                    gw,
                    bot_token=token,
                    channel_id=str(sec.get("channel_id") or ""),
                )
            )
            registered.append("slack")

    if "mattermost" in enabled:
        sec = _section(cfg, "mattermost")
        token = resolve_channel_secret(cfg, "mattermost", "bot_token", home=home)
        base_url = str(sec.get("base_url") or "").strip()
        if token and base_url:
            gw.register_channel(
                MattermostChannel(
                    gw,
                    bot_token=token,
                    base_url=base_url,
                    channel_id=str(sec.get("channel_id") or ""),
                )
            )
            registered.append("mattermost")
        else:
            logger.warning("mattermost enabled but missing bot_token or base_url")

    for mid in ("whatsapp", "teams", "matrix", "google_chat", "signal"):
        if mid not in enabled:
            continue
        try:
            kind = ChannelKind(mid)
        except ValueError:
            continue
        mdef = get_messenger(mid)
        gw.register_channel(
            PlannedMessengerChannel(kind, gw, label=mdef.name if mdef else mid)
        )
        registered.append(mid)

    return registered
