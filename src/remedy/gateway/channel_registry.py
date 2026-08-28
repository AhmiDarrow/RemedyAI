"""Register enabled messenger adapters from config (slim factory)."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from remedy.gateway.channels import (
    DiscordChannel,
    GoogleChatChannel,
    MatrixChannel,
    MattermostChannel,
    SignalChannel,
    SlackChannel,
    TeamsChannel,
    TelegramChannel,
    WhatsAppChannel,
)
from remedy.gateway.messengers import parse_list_field, resolve_channel_secret
from remedy.gateway.router import Gateway
from remedy.home import default_home

logger = logging.getLogger(__name__)


def _sec(cfg: dict[str, Any], channel: str) -> dict[str, Any]:
    raw = cfg.get(channel)
    return dict(raw) if isinstance(raw, dict) else {}


def _home(cfg: dict[str, Any]) -> Path | None:
    """Resolve Remedy home for poll locks / offsets (must be stable across processes)."""
    import os

    h = cfg.get("home_dir")
    if h:
        return Path(str(h)).expanduser()
    env = (os.environ.get("REMEDY_HOME") or "").strip()
    if env:
        return Path(env).expanduser()
    # Default matches CLI/desktop --home fallback so dual instances share one lock.
    return default_home()


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
                    home_dir=str(home) if home else None,
                )
            )
            registered.append("telegram")
        else:
            logger.warning("telegram enabled but no bot_token")

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
                    home_dir=str(home) if home else None,
                )
            )
            registered.append("discord")
        else:
            logger.warning("discord enabled but no bot_token")

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
                    home_dir=str(home) if home else None,
                )
            )
            registered.append("slack")
        else:
            logger.warning("slack enabled but no bot_token")

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
                    home_dir=str(home) if home else None,
                )
            )
            registered.append("mattermost")
        else:
            logger.warning("mattermost enabled but missing bot_token or base_url")

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
                    home_dir=str(home) if home else None,
                )
            )
            registered.append("matrix")
        else:
            logger.warning("matrix enabled but missing access_token or homeserver")

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

    if "teams" in enabled:
        s = _sec(cfg, "teams")
        app_id = str(s.get("app_id") or "").strip()
        pwd = _secret(cfg, "teams", "app_password", home)
        if app_id and pwd:
            gw.register_channel(
                TeamsChannel(
                    gw,
                    app_id=app_id,
                    app_password=pwd,
                    tenant_id=str(s.get("tenant_id") or ""),
                    allow_ids=parse_list_field(s.get("allow_ids")),
                    allow_all=bool(s.get("allow_all")),
                )
            )
            registered.append("teams")
        else:
            logger.warning("teams enabled but missing app_id or app_password")

    if "google_chat" in enabled:
        s = _sec(cfg, "google_chat")
        tok = _secret(cfg, "google_chat", "access_token", home)
        if tok:
            gw.register_channel(
                GoogleChatChannel(
                    gw,
                    access_token=tok,
                    space_id=str(s.get("space_id") or ""),
                    allow_ids=parse_list_field(s.get("allow_ids")),
                    allow_all=bool(s.get("allow_all")),
                )
            )
            registered.append("google_chat")
        else:
            logger.warning("google_chat enabled but no access_token")

    if "signal" in enabled:
        s = _sec(cfg, "signal")
        gw.register_channel(
            SignalChannel(
                gw,
                cli_path=str(s.get("cli_path") or "signal-cli"),
                account=str(s.get("account") or ""),
                allow_from=parse_list_field(s.get("allow_from") or s.get("allow_ids")),
                allow_all=bool(s.get("allow_all")),
                home_dir=str(home) if home else None,
            )
        )
        registered.append("signal")

    return registered
