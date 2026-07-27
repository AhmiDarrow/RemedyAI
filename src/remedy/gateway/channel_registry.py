"""Register enabled messenger adapters from config (slim factory)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from remedy.gateway.channels import (
    DiscordChannel,
    MatrixChannel,
    MattermostChannel,
    PlannedMessengerChannel,
    SlackChannel,
    TelegramChannel,
    WhatsAppChannel,
)
from remedy.gateway.messengers import (
    get_messenger,
    parse_list_field,
    resolve_channel_secret,
)
from remedy.gateway.router import Gateway
from remedy.models import ChannelKind

logger = logging.getLogger(__name__)


def _sec(cfg: dict[str, Any], channel: str) -> dict[str, Any]:
    raw = cfg.get(channel)
    return dict(raw) if isinstance(raw, dict) else {}


def _home(cfg: dict[str, Any]) -> Path | None:
    h = cfg.get("home_dir")
    return Path(str(h)).expanduser() if h else None


def _secret(cfg: dict, channel: str, key: str, home: Path | None, override: str = "") -> str:
    if override:
        return override
    return resolve_channel_secret(cfg, channel, key, home=home)


def register_messenger_channels(
    gw: Gateway,
    cfg: dict[str, Any],
    *,
    token_telegram: str = "",
    token_discord: str = "",
    token_slack: str = "",
) -> list[str]:
    home = _home(cfg)
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

    # --- Telegram ---
    if "telegram" in enabled:
        s = _sec(cfg, "telegram")
        tok = _secret(cfg, "telegram", "bot_token", home, token_telegram)
        if tok:
            gw.register_channel(
                TelegramChannel(
                    gw,
                    bot_token=tok,
                    chat_ids=parse_list_field(s.get("allow_chat_ids") or s.get("chat_ids")),
                    allow_all=bool(s.get("allow_all")),
                )
            )
            registered.append("telegram")
        else:
            logger.warning("telegram enabled but no bot_token")

    # --- Discord ---
    if "discord" in enabled:
        s = _sec(cfg, "discord")
        tok = _secret(cfg, "discord", "bot_token", home, token_discord)
        if tok:
            gw.register_channel(
                DiscordChannel(
                    gw,
                    bot_token=tok,
                    channel_id=str(s.get("channel_id") or ""),
                    guild_id=str(s.get("guild_id") or ""),
                    allow_ids=parse_list_field(s.get("allow_ids") or s.get("allow_chat_ids")),
                    allow_all=bool(s.get("allow_all")),
                )
            )
            registered.append("discord")
        else:
            logger.warning("discord enabled but no bot_token")

    # --- Slack ---
    if "slack" in enabled:
        s = _sec(cfg, "slack")
        tok = _secret(cfg, "slack", "bot_token", home, token_slack)
        if tok:
            gw.register_channel(
                SlackChannel(
                    gw,
                    bot_token=tok,
                    app_token=_secret(cfg, "slack", "app_token", home)
                    or str(s.get("app_token") or ""),
                    channel_id=str(s.get("channel_id") or ""),
                    allow_ids=parse_list_field(s.get("allow_ids") or s.get("allow_chat_ids")),
                    allow_all=bool(s.get("allow_all")),
                )
            )
            registered.append("slack")
        else:
            logger.warning("slack enabled but no bot_token")

    # --- Mattermost ---
    if "mattermost" in enabled:
        s = _sec(cfg, "mattermost")
        tok = _secret(cfg, "mattermost", "bot_token", home)
        base = str(s.get("base_url") or "").strip()
        if tok and base:
            gw.register_channel(
                MattermostChannel(
                    gw,
                    bot_token=tok,
                    base_url=base,
                    channel_id=str(s.get("channel_id") or ""),
                    team_id=str(s.get("team_id") or ""),
                    allow_ids=parse_list_field(s.get("allow_ids") or s.get("allow_chat_ids")),
                    allow_all=bool(s.get("allow_all")),
                )
            )
            registered.append("mattermost")
        else:
            logger.warning("mattermost enabled but missing bot_token or base_url")

    # --- Matrix ---
    if "matrix" in enabled:
        s = _sec(cfg, "matrix")
        tok = _secret(cfg, "matrix", "access_token", home)
        hs = str(s.get("homeserver") or "").strip()
        if tok and hs:
            gw.register_channel(
                MatrixChannel(
                    gw,
                    access_token=tok,
                    homeserver=hs,
                    user_id=str(s.get("user_id") or ""),
                    room_id=str(s.get("room_id") or ""),
                    allow_ids=parse_list_field(s.get("allow_ids") or s.get("room_id")),
                    allow_all=bool(s.get("allow_all")),
                )
            )
            registered.append("matrix")
        else:
            logger.warning("matrix enabled but missing access_token or homeserver")

    # --- WhatsApp ---
    if "whatsapp" in enabled:
        s = _sec(cfg, "whatsapp")
        tok = _secret(cfg, "whatsapp", "access_token", home)
        phone = str(s.get("phone_number_id") or "").strip()
        if tok and phone:
            gw.register_channel(
                WhatsAppChannel(
                    gw,
                    access_token=tok,
                    phone_number_id=phone,
                    verify_token=_secret(cfg, "whatsapp", "verify_token", home)
                    or str(s.get("verify_token") or ""),
                    app_secret=_secret(cfg, "whatsapp", "app_secret", home),
                    allow_from=parse_list_field(s.get("allow_from")),
                    allow_all=bool(s.get("allow_all")),
                )
            )
            registered.append("whatsapp")
        else:
            logger.warning("whatsapp enabled but missing access_token or phone_number_id")

    # --- Planned stubs ---
    for mid in ("teams", "google_chat", "signal"):
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
