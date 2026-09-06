"""Messenger settings helpers — TestClient loads Go JSON catalog fail-closed.

Production Settings field_schema lives in Go ``native/go/httpapi`` via
embedded ``messenger_catalog.json``. Python has no catalog twin: helpers here
read that fixture for in-process TestClient / secret scrub / CLI only.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

MessengerStatus = Literal["ready", "needs_setup", "planned", "partial"]
FieldKind = Literal["secret", "text", "bool", "list", "url"]

SECRET_FIELD_KEYS = frozenset(
    {
        "bot_token",
        "access_token",
        "refresh_token",
        "oauth_client_secret",
        "app_token",
        "app_password",
        "app_secret",
        "verify_token",
        "signing_secret",
    }
)
INTERNAL_CHANNELS = frozenset({"cli", "web", "api"})

_TG_BOT_URL_RE = re.compile('(?i)(https?://api\\.telegram\\.org/bot)([^/\\s<>\\"\']+)')
_TG_BOT_TOKEN_RE = re.compile('\\b(\\d{6,12}:[A-Za-z0-9_-]{20,})\\b')
_SLACK_XOX_RE = re.compile(r"(?i)\bxox[baprs]-[A-Za-z0-9-]{10,}")
_SLACK_XAPP_RE = re.compile(r"(?i)\bxapp-[A-Za-z0-9-]{10,}")
_DISCORD_WEBHOOK_RE = re.compile(
    r"(?i)(https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/)([A-Za-z0-9_\-]+)"
)
_DISCORD_BOT_TOKEN_RE = re.compile(
    r"\b([A-Za-z0-9_\-]{20,40}\.[A-Za-z0-9_\-]{4,10}\.[A-Za-z0-9_\-]{20,})\b"
)
_MATRIX_SYT_RE = re.compile(r"\bsyt_[A-Za-z0-9._\-]{16,}\b")
_BEARER_RE = re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}")


@dataclass(frozen=True)
class MessengerField:
    key: str
    label: str
    kind: FieldKind = "text"
    placeholder: str = ""
    help: str = ""
    required: bool = False


@dataclass(frozen=True)
class MessengerDef:
    id: str
    name: str
    description: str
    status: MessengerStatus
    inbound: bool
    outbound: bool
    docs_url: str = ""
    fields: tuple[MessengerField, ...] = ()
    max_reply_chars: int = 4000
    badge: str = ""

    @property
    def origin_label(self) -> str:
        return self.badge or self.name


def _catalog_json_path() -> Path:
    """Resolve Go SSOT fixture only — no bundled Python soft copy."""
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "native" / "go" / "httpapi" / "messenger_catalog.json"
        if candidate.is_file():
            return candidate
    raise FileNotFoundError(
        "messenger_catalog.json not found under native/go/httpapi (Go SSOT)"
    )


def _entry_from_raw(raw: dict[str, Any]) -> MessengerDef:
    fields = tuple(
        MessengerField(
            key=str(f.get("key") or ""),
            label=str(f.get("label") or ""),
            kind=str(f.get("kind") or "text"),  # type: ignore[arg-type]
            placeholder=str(f.get("placeholder") or ""),
            help=str(f.get("help") or ""),
            required=bool(f.get("required") or False),
        )
        for f in (raw.get("fields") or [])
        if isinstance(f, dict) and str(f.get("key") or "").strip()
    )
    status = str(raw.get("status") or "planned")
    if status == "partial":
        # Catalog no longer ships partial; map legacy fixtures to needs_setup.
        status = "needs_setup"
    if status not in ("ready", "needs_setup", "planned"):
        status = "planned"
    return MessengerDef(
        id=str(raw.get("id") or "").strip().lower(),
        name=str(raw.get("name") or ""),
        description=str(raw.get("description") or ""),
        status=status,  # type: ignore[arg-type]
        inbound=bool(raw.get("inbound")),
        outbound=bool(raw.get("outbound")),
        docs_url=str(raw.get("docs_url") or ""),
        fields=fields,
        max_reply_chars=int(raw.get("max_reply_chars") or 4000),
        badge=str(raw.get("badge") or ""),
    )


@lru_cache(maxsize=1)
def _load_catalog() -> tuple[MessengerDef, ...]:
    data = json.loads(_catalog_json_path().read_text(encoding="utf-8"))
    if not isinstance(data, list) or not data:
        raise ValueError("messenger_catalog.json must be a non-empty list")
    return tuple(_entry_from_raw(row) for row in data if isinstance(row, dict))


def list_messenger_definitions() -> list[MessengerDef]:
    return list(_load_catalog())


def get_messenger(messenger_id: str) -> MessengerDef | None:
    mid = str(messenger_id or "").strip().lower()
    for m in _load_catalog():
        if m.id == mid:
            return m
    return None


def messenger_ids() -> list[str]:
    return [m.id for m in _load_catalog()]


def is_messenger_channel(channel: str) -> bool:
    return get_messenger(channel) is not None


def redact_messenger_secrets(text: str) -> str:
    """Scrub messenger tokens/URLs from free text before logging or display."""
    if not text:
        return ""
    out = _TG_BOT_URL_RE.sub(r"\1[redacted]", str(text))
    out = _TG_BOT_TOKEN_RE.sub("[redacted]", out)
    out = _SLACK_XOX_RE.sub("xox[redacted]", out)
    out = _SLACK_XAPP_RE.sub("xapp[redacted]", out)
    out = _DISCORD_WEBHOOK_RE.sub(r"\1[redacted]", out)
    out = _DISCORD_BOT_TOKEN_RE.sub("[redacted]", out)
    out = _MATRIX_SYT_RE.sub("[redacted]", out)
    out = _BEARER_RE.sub("Bearer [redacted]", out)
    try:
        from remedy.core.metabolism.redact import redact_text

        out = redact_text(out)
    except Exception:
        return "[redacted]"
    return out


def external_session_id(channel: str, external_chat_id: str, *, thread_id: str = "") -> str:
    ch = re.sub(r"[^a-z0-9_]+", "", str(channel or "").strip().lower()) or "unknown"
    ext = str(external_chat_id or "").strip() or "default"
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
    ch = str(channel or "").strip().lower()
    fk = str(field_key or "").strip().lower()
    return f"ch:{ch}:{fk}"


def secret_field_keys_for(channel: str) -> list[str]:
    mdef = get_messenger(channel)
    if not mdef:
        return []
    return [f.key for f in mdef.fields if f.kind == "secret"]


def public_fields_from_section(channel: str, section: dict[str, Any] | None) -> dict[str, Any]:
    mdef = get_messenger(channel)
    raw = dict(section or {}) if isinstance(section, dict) else {}
    if not mdef:

        def _is_secret_key(k: str) -> bool:
            kl = str(k).lower()
            return (
                kl in SECRET_FIELD_KEYS
                or kl.endswith("_token")
                or kl.endswith("_password")
                or kl.endswith("_secret")
            )

        return {k: v for k, v in raw.items() if not _is_secret_key(k)}
    out: dict[str, Any] = {}
    for f in mdef.fields:
        if f.kind == "secret" or f.key not in raw:
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
    mdef = get_messenger(channel)
    out = dict(section or {})
    secret_keys = set(secret_field_keys_for(channel)) | set(SECRET_FIELD_KEYS)
    for key, val in (updates or {}).items():
        k = str(key).strip()
        if not k or k in ("enabled", "clear_token"):
            continue
        if k in secret_keys or k.endswith("_token") or k in ("bot_token", "access_token", "app_password"):
            continue
        if mdef:
            fdef = next((f for f in mdef.fields if f.key == k), None)
            if fdef is None or fdef.kind == "secret":
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
    enabled_raw = cfg.get("enabled_channels") or []
    if not isinstance(enabled_raw, list):
        enabled_raw = [enabled_raw] if enabled_raw else []
    enabled = {str(x).strip().lower() for x in enabled_raw if str(x).strip()}
    secrets_set = secrets_set or {}
    result: list[dict[str, Any]] = []
    for mdef in _load_catalog():
        section = cfg.get(mdef.id)
        if not isinstance(section, dict):
            section = {}
        token_set = False
        for sk in secret_field_keys_for(mdef.id):
            store_key = channel_secret_store_key(mdef.id, sk)
            if secrets_set.get(store_key) or secrets_set.get(f"ch:{mdef.id}:{sk}"):
                token_set = True
                break
            if str(section.get(sk) or "").strip():
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
    import os

    from remedy.interfaces.secret_store import get_provider_secret

    ch = str(channel or "").strip().lower()
    fk = str(field_key or "bot_token").strip().lower()
    stored = get_provider_secret(channel_secret_store_key(ch, fk), home=home)
    if stored:
        return stored
    section = cfg.get(ch) if isinstance(cfg, dict) else None
    if isinstance(section, dict):
        legacy = str(section.get(fk) or "").strip()
        if legacy:
            return legacy
    env_key = f"REMEDY_{ch.upper()}__{fk.upper()}"
    return str(os.environ.get(env_key, "") or "").strip()


def messengers_for_settings_response(
    cfg: dict[str, Any],
    home_path: Path | None,
) -> tuple[list[str], list[dict[str, Any]]]:
    """Return (enabled_channels, messengers public list)."""
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
