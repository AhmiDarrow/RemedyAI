"""Messenger settings helpers — keep routes/settings.py thin.

Public status for GET, apply updates for PUT, no FastAPI dependency.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def messengers_for_settings_response(
    cfg: dict[str, Any],
    home_path: Path | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (enabled_channels, messengers public list)."""
    from remedy.gateway.messengers import (
        build_messenger_public_status,
        channel_secret_store_key,
        list_messenger_definitions,
        secret_field_keys_for,
    )
    from remedy.interfaces.secret_store import load_provider_keys

    keys = load_provider_keys(home_path)
    secrets_set = dict.fromkeys(keys, True)
    for mdef in list_messenger_definitions():
        sec = cfg.get(mdef.id)
        if not isinstance(sec, dict):
            continue
        for sk in secret_field_keys_for(mdef.id):
            if str(sec.get(sk) or "").strip():
                secrets_set[channel_secret_store_key(mdef.id, sk)] = True

    enabled_raw = cfg.get("enabled_channels") or ["cli"]
    if not isinstance(enabled_raw, list):
        enabled_raw = [enabled_raw] if enabled_raw else ["cli"]
    enabled = [str(x) for x in enabled_raw]
    messengers = build_messenger_public_status(cfg, secrets_set=secrets_set)
    return enabled, messengers


def normalize_enabled_channels(raw: Any) -> list[str]:
    if isinstance(raw, list):
        chs = [str(x).strip().lower() for x in raw if str(x).strip()]
    else:
        chs = ["cli"]
    if "cli" not in chs:
        chs.insert(0, "cli")
    return chs


def apply_messengers_update(
    cfg: dict[str, Any],
    messengers_update: dict[str, Any],
    *,
    home_path: Path | None,
) -> dict[str, Any]:
    """Merge messenger field/secret updates into cfg (mutates and returns cfg)."""
    from remedy.gateway.messengers import (
        apply_messenger_field_updates,
        channel_secret_store_key,
        messenger_ids,
        secret_field_keys_for,
    )
    from remedy.interfaces.secret_store import set_provider_secret

    if not isinstance(messengers_update, dict):
        return cfg

    enabled = {
        str(x).strip().lower()
        for x in (cfg.get("enabled_channels") or [])
        if str(x).strip()
    }
    known = set(messenger_ids())

    for mid, body in messengers_update.items():
        mid = str(mid or "").strip().lower()
        if mid not in known or not isinstance(body, dict):
            continue
        if body.get("enabled") is True:
            enabled.add(mid)
        elif body.get("enabled") is False:
            enabled.discard(mid)

        section = dict(cfg.get(mid) or {}) if isinstance(cfg.get(mid), dict) else {}
        secret_keys = secret_field_keys_for(mid)

        for sk in secret_keys:
            if sk in body and body[sk] is not None and str(body[sk]).strip():
                set_provider_secret(
                    channel_secret_store_key(mid, sk),
                    str(body[sk]).strip(),
                    home=home_path,
                )
                section.pop(sk, None)
            if body.get("clear_token") or body.get(f"clear_{sk}"):
                set_provider_secret(
                    channel_secret_store_key(mid, sk),
                    None,
                    home=home_path,
                )
                section.pop(sk, None)

        for alias in ("bot_token", "access_token", "app_password"):
            if alias in body and body[alias] is not None and str(body[alias]).strip():
                target = alias if alias in secret_keys else (secret_keys[0] if secret_keys else alias)
                set_provider_secret(
                    channel_secret_store_key(mid, target),
                    str(body[alias]).strip(),
                    home=home_path,
                )
                section.pop(target, None)
                section.pop(alias, None)

        section = apply_messenger_field_updates(section, body, channel=mid)
        cfg[mid] = section

    ch_list = sorted(enabled) if enabled else ["cli"]
    if "cli" not in ch_list:
        ch_list.insert(0, "cli")
    cfg["enabled_channels"] = ch_list
    return cfg
