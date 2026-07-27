"""Messenger connector catalog — single source of truth for desktop + gateway.

Defines which messaging platforms Remedy offers, their config fields, capability
status, and helpers for stable session identity + public (secret-scrubbed) views.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Literal

MessengerStatus = Literal["ready", "partial", "planned"]
FieldKind = Literal["secret", "text", "bool", "list", "url"]


@dataclass(frozen=True)
class MessengerField:
    """One configurable field on a messenger connector."""

    key: str
    label: str
    kind: FieldKind = "text"
    placeholder: str = ""
    help: str = ""
    required: bool = False


@dataclass(frozen=True)
class MessengerDef:
    """Catalog entry for one messenger platform."""

    id: str
    name: str
    description: str
    status: MessengerStatus
    inbound: bool
    outbound: bool
    docs_url: str = ""
    fields: tuple[MessengerField, ...] = ()
    # Platform reply soft cap (chars); adapters may split longer text.
    max_reply_chars: int = 4000
    # Shown in desktop sidebar badge
    badge: str = ""

    @property
    def origin_label(self) -> str:
        return self.badge or self.name


# Secret field keys commonly used (for migration / scrub)
SECRET_FIELD_KEYS = frozenset(
    {
        "bot_token",
        "access_token",
        "app_password",
        "app_secret",
        "verify_token",
        "signing_secret",
    }
)

MESSENGERS: tuple[MessengerDef, ...] = (
    MessengerDef(
        id="telegram",
        name="Telegram",
        description="Bot API long-poll inbound and sendMessage outbound.",
        status="ready",
        inbound=True,
        outbound=True,
        docs_url="https://core.telegram.org/bots#how-do-i-create-a-bot",
        badge="Telegram",
        max_reply_chars=4096,
        fields=(
            MessengerField(
                "bot_token",
                "Bot token",
                "secret",
                "123456:ABC…",
                "From @BotFather",
                required=True,
            ),
            MessengerField(
                "allow_chat_ids",
                "Allowed chat IDs",
                "list",
                "123456789, -100…",
                "Comma-separated. Empty + allow_all off = ignore all chats.",
            ),
            MessengerField(
                "allow_all",
                "Allow all chats",
                "bool",
                help="Dangerous for public bots. Prefer an allowlist.",
            ),
        ),
    ),
    MessengerDef(
        id="discord",
        name="Discord",
        description="Gateway WS inbound + REST outbound. Allowlist channels/users.",
        status="ready",
        inbound=True,
        outbound=True,
        docs_url="https://discord.com/developers/docs/getting-started",
        badge="Discord",
        max_reply_chars=2000,
        fields=(
            MessengerField("bot_token", "Bot token", "secret", required=True),
            MessengerField("channel_id", "Default channel ID", "text"),
            MessengerField("guild_id", "Guild ID", "text"),
            MessengerField(
                "allow_ids",
                "Allowed IDs",
                "list",
                help="Channel, user, or guild ids. Empty + allow_all off = ignore all.",
            ),
            MessengerField("allow_all", "Allow all", "bool"),
        ),
    ),
    MessengerDef(
        id="slack",
        name="Slack",
        description="Socket Mode inbound + chat.postMessage. Needs bot + app token.",
        status="ready",
        inbound=True,
        outbound=True,
        docs_url="https://api.slack.com/apis/connections/socket",
        badge="Slack",
        max_reply_chars=3000,
        fields=(
            MessengerField("bot_token", "Bot token", "secret", "xoxb-…", required=True),
            MessengerField(
                "app_token",
                "App-level token",
                "secret",
                "xapp-…",
                "Socket Mode (connections:write).",
            ),
            MessengerField("channel_id", "Default channel ID", "text"),
            MessengerField("allow_ids", "Allowed channel/user IDs", "list"),
            MessengerField("allow_all", "Allow all", "bool"),
        ),
    ),
    MessengerDef(
        id="mattermost",
        name="Mattermost",
        description="WebSocket inbound + REST posts (self-hosted).",
        status="ready",
        inbound=True,
        outbound=True,
        docs_url="https://developers.mattermost.com/integrate/reference/bot-accounts/",
        badge="Mattermost",
        max_reply_chars=4000,
        fields=(
            MessengerField(
                "base_url",
                "Server URL",
                "url",
                "https://chat.example.com",
                required=True,
            ),
            MessengerField("bot_token", "Bot token", "secret", required=True),
            MessengerField("team_id", "Team ID", "text"),
            MessengerField("channel_id", "Default channel ID", "text"),
            MessengerField("allow_ids", "Allowed channel/user IDs", "list"),
            MessengerField("allow_all", "Allow all", "bool"),
        ),
    ),
    MessengerDef(
        id="whatsapp",
        name="WhatsApp",
        description="Cloud API outbound; inbound via /api/webhooks/whatsapp (public HTTPS).",
        status="partial",
        inbound=True,
        outbound=True,
        docs_url="https://developers.facebook.com/docs/whatsapp/cloud-api",
        badge="WhatsApp",
        max_reply_chars=4096,
        fields=(
            MessengerField("access_token", "Access token", "secret", required=True),
            MessengerField("phone_number_id", "Phone number ID", "text", required=True),
            MessengerField("verify_token", "Webhook verify token", "secret"),
            MessengerField("app_secret", "App secret (HMAC)", "secret"),
            MessengerField(
                "allow_from",
                "Allowed phone numbers",
                "list",
                help="E.164 numbers, comma-separated.",
            ),
            MessengerField("allow_all", "Allow all senders", "bool"),
        ),
    ),
    MessengerDef(
        id="teams",
        name="Microsoft Teams",
        description="Bot Framework / Teams channel (planned).",
        status="planned",
        inbound=False,
        outbound=False,
        docs_url="https://learn.microsoft.com/en-us/microsoftteams/platform/bots/what-are-bots",
        badge="Teams",
        max_reply_chars=28000,
        fields=(
            MessengerField("app_id", "App (client) ID", "text"),
            MessengerField("app_password", "App password / secret", "secret"),
            MessengerField("tenant_id", "Tenant ID", "text"),
        ),
    ),
    MessengerDef(
        id="matrix",
        name="Matrix",
        description="Client-Server /sync inbound + room send.",
        status="ready",
        inbound=True,
        outbound=True,
        docs_url="https://spec.matrix.org/latest/client-server-api/",
        badge="Matrix",
        max_reply_chars=4000,
        fields=(
            MessengerField(
                "homeserver",
                "Homeserver URL",
                "url",
                "https://matrix.org",
                required=True,
            ),
            MessengerField("access_token", "Access token", "secret", required=True),
            MessengerField("user_id", "Bot user ID", "text", "@bot:example.com"),
            MessengerField("room_id", "Default room ID", "text"),
            MessengerField("allow_ids", "Allowed room/user IDs", "list"),
            MessengerField("allow_all", "Allow all rooms", "bool"),
        ),
    ),
    MessengerDef(
        id="google_chat",
        name="Google Chat",
        description="Google Chat API (planned).",
        status="planned",
        inbound=False,
        outbound=False,
        docs_url="https://developers.google.com/chat",
        badge="GChat",
        max_reply_chars=4096,
        fields=(
            MessengerField("access_token", "Access token / SA JSON path", "secret"),
            MessengerField("space_id", "Default space ID", "text"),
        ),
    ),
    MessengerDef(
        id="signal",
        name="Signal",
        description="Optional signal-cli bridge (planned).",
        status="planned",
        inbound=False,
        outbound=False,
        docs_url="https://github.com/AsamK/signal-cli",
        badge="Signal",
        max_reply_chars=2000,
        fields=(
            MessengerField("cli_path", "signal-cli path", "text", "signal-cli"),
            MessengerField("account", "Account number", "text", "+1…"),
        ),
    ),
)

_BY_ID: dict[str, MessengerDef] = {m.id: m for m in MESSENGERS}

# Internal channels not shown as "messengers" in Settings
INTERNAL_CHANNELS = frozenset({"cli", "web", "api"})


def list_messenger_definitions() -> list[MessengerDef]:
    return list(MESSENGERS)


def get_messenger(messenger_id: str) -> MessengerDef | None:
    return _BY_ID.get(str(messenger_id or "").strip().lower())


def messenger_ids() -> list[str]:
    return [m.id for m in MESSENGERS]


def is_messenger_channel(channel: str) -> bool:
    return str(channel or "").strip().lower() in _BY_ID


def external_session_id(channel: str, external_chat_id: str, *, thread_id: str = "") -> str:
    """Stable chat_session id for a messenger conversation.

    Format: ``msg:{channel}:{chat_id}`` or ``msg:{channel}:{chat_id}:{thread}``.
    """
    ch = re.sub(r"[^a-z0-9_]+", "", str(channel or "").strip().lower()) or "unknown"
    ext = str(external_chat_id or "").strip()
    if not ext:
        ext = "default"
    # Keep id filesystem / sqlite friendly
    ext = re.sub(r"[^\w.@+-]+", "_", ext)[:180]
    tid = str(thread_id or "").strip()
    if tid:
        tid = re.sub(r"[^\w.@+-]+", "_", tid)[:80]
        return f"msg:{ch}:{ext}:{tid}"
    return f"msg:{ch}:{ext}"


def heuristic_session_title(
    channel: str,
    *,
    username: str | None = None,
    chat_title: str | None = None,
    first_message: str | None = None,
) -> str:
    """Fast title without calling a model."""
    mdef = get_messenger(channel)
    label = mdef.origin_label if mdef else (channel or "Messenger").title()
    if chat_title and str(chat_title).strip():
        return f"{label} · {str(chat_title).strip()[:60]}"
    if username and str(username).strip():
        u = str(username).strip()
        if not u.startswith("@"):
            u = f"@{u}" if mdef and mdef.id == "telegram" else u
        return f"{label} · {u[:60]}"
    if first_message and str(first_message).strip():
        snippet = " ".join(str(first_message).split())[:48]
        if len(str(first_message).strip()) > 48:
            snippet += "…"
        return f"{label} · {snippet}"
    return f"{label} chat"


def max_reply_chars(channel: str) -> int:
    mdef = get_messenger(channel)
    return int(mdef.max_reply_chars) if mdef else 4000


def split_message(text: str, channel: str) -> list[str]:
    """Split a long reply to fit platform limits."""
    limit = max_reply_chars(channel)
    text = text or ""
    if len(text) <= limit:
        return [text] if text else []
    parts: list[str] = []
    rest = text
    while rest:
        if len(rest) <= limit:
            parts.append(rest)
            break
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 3:
            cut = rest.rfind(" ", 0, limit)
        if cut < limit // 3:
            cut = limit
        parts.append(rest[:cut].rstrip())
        rest = rest[cut:].lstrip()
    return parts


def channel_secret_store_key(channel: str, field_key: str) -> str:
    """Key used inside the secure secret store for a channel credential."""
    ch = str(channel or "").strip().lower()
    fk = str(field_key or "").strip().lower()
    return f"ch:{ch}:{fk}"


def secret_field_keys_for(channel: str) -> list[str]:
    mdef = get_messenger(channel)
    if not mdef:
        return []
    return [f.key for f in mdef.fields if f.kind == "secret"]


def public_fields_from_section(channel: str, section: dict[str, Any] | None) -> dict[str, Any]:
    """Non-secret field values safe for API responses."""
    mdef = get_messenger(channel)
    raw = dict(section or {}) if isinstance(section, dict) else {}
    if not mdef:
        # Strip known secret keys
        return {
            k: v
            for k, v in raw.items()
            if str(k).lower() not in SECRET_FIELD_KEYS and not str(k).lower().endswith("_token")
        }
    out: dict[str, Any] = {}
    for f in mdef.fields:
        if f.kind == "secret":
            continue
        if f.key not in raw:
            continue
        val = raw[f.key]
        if f.kind == "list":
            if isinstance(val, list):
                out[f.key] = [str(x).strip() for x in val if str(x).strip()]
            elif isinstance(val, str) and val.strip():
                out[f.key] = [x.strip() for x in val.split(",") if x.strip()]
            else:
                out[f.key] = []
        elif f.kind == "bool":
            if isinstance(val, bool):
                out[f.key] = val
            else:
                out[f.key] = str(val).strip().lower() in ("1", "true", "yes", "on")
        else:
            out[f.key] = "" if val is None else str(val)
    return out


def parse_list_field(val: Any) -> list[str]:
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str) and val.strip():
        return [x.strip() for x in val.replace(";", ",").split(",") if x.strip()]
    return []


def apply_messenger_field_updates(
    section: dict[str, Any],
    updates: dict[str, Any],
    *,
    channel: str,
) -> dict[str, Any]:
    """Merge non-secret field updates into a channel config section (mutates copy)."""
    mdef = get_messenger(channel)
    out = dict(section or {})
    secret_keys = set(secret_field_keys_for(channel)) | set(SECRET_FIELD_KEYS)
    for key, val in (updates or {}).items():
        k = str(key).strip()
        if not k or k in secret_keys or k in ("enabled", "clear_token", "bot_token", "access_token"):
            # secrets handled separately
            if k in ("enabled",):
                continue
            if k in secret_keys or k.endswith("_token") or k in ("bot_token", "access_token", "app_password"):
                continue
        if mdef:
            fdef = next((f for f in mdef.fields if f.key == k), None)
            if fdef is None:
                continue
            if fdef.kind == "secret":
                continue
            if fdef.kind == "list":
                out[k] = parse_list_field(val)
            elif fdef.kind == "bool":
                if isinstance(val, bool):
                    out[k] = val
                else:
                    out[k] = str(val).strip().lower() in ("1", "true", "yes", "on")
            else:
                out[k] = "" if val is None else str(val).strip()
        else:
            out[k] = val
    return out


def build_messenger_public_status(
    cfg: dict[str, Any],
    *,
    secrets_set: dict[str, bool] | None = None,
) -> list[dict[str, Any]]:
    """List of public messenger status dicts for Settings GET."""
    enabled_raw = cfg.get("enabled_channels") or []
    if not isinstance(enabled_raw, list):
        enabled_raw = [enabled_raw] if enabled_raw else []
    enabled = {str(x).strip().lower() for x in enabled_raw if str(x).strip()}
    secrets_set = secrets_set or {}
    result: list[dict[str, Any]] = []
    for mdef in MESSENGERS:
        section = cfg.get(mdef.id)
        if not isinstance(section, dict):
            section = {}
        # Token set: secret store OR legacy plaintext in section
        token_set = False
        for sk in secret_field_keys_for(mdef.id):
            store_key = channel_secret_store_key(mdef.id, sk)
            if secrets_set.get(store_key) or secrets_set.get(f"ch:{mdef.id}:{sk}"):
                token_set = True
                break
            legacy = str(section.get(sk) or "").strip()
            if legacy:
                token_set = True
                break
        # Also check generic bot_token / access_token flags from aggregate map
        if not token_set:
            for sk in secret_field_keys_for(mdef.id):
                if secrets_set.get(channel_secret_store_key(mdef.id, sk)):
                    token_set = True
                    break
        result.append(
            {
                "id": mdef.id,
                "name": mdef.name,
                "description": mdef.description,
                "status": mdef.status,
                "enabled": mdef.id in enabled,
                "token_set": token_set,
                "inbound": mdef.inbound,
                "outbound": mdef.outbound,
                "docs_url": mdef.docs_url,
                "badge": mdef.origin_label,
                "max_reply_chars": mdef.max_reply_chars,
                "fields": public_fields_from_section(mdef.id, section),
                "field_schema": [
                    {
                        "key": f.key,
                        "label": f.label,
                        "kind": f.kind,
                        "placeholder": f.placeholder,
                        "help": f.help,
                        "required": f.required,
                    }
                    for f in mdef.fields
                ],
            }
        )
    return result


def resolve_channel_secret(
    cfg: dict[str, Any],
    channel: str,
    field_key: str = "bot_token",
    *,
    home: Any = None,
) -> str:
    """Resolve a channel secret: secure store > config section > env."""
    import os

    from remedy.interfaces.secret_store import get_provider_secret

    ch = str(channel or "").strip().lower()
    fk = str(field_key or "bot_token").strip().lower()
    store_key = channel_secret_store_key(ch, fk)
    stored = get_provider_secret(store_key, home=home)
    if stored:
        return stored
    section = cfg.get(ch) if isinstance(cfg, dict) else None
    if isinstance(section, dict):
        legacy = str(section.get(fk) or "").strip()
        if legacy:
            return legacy
    env_key = f"REMEDY_{ch.upper()}__{fk.upper()}"
    return str(os.environ.get(env_key, "") or "").strip()
