"""House rules: first-run conversation, talk-to-change, audit, and rollback.

House rules are the owner's preferences for how Remedy should live with them.
They are set in a short, skippable first-run conversation (or the traditional
form), in Settings, or later by talking with Remedy. Peers and other bots
can never change them.

Storage under ``{home}``:

- ``house_rules.toml`` — current answers and provenance (human-readable)
- ``setup_session.json`` — installer/setup draft, mode, and mind-gate status
- ``house_rules_pending.json`` — unconfirmed sensitive change, if any
- ``house_rules_prev.json`` — previous snapshot for one-step rollback
- ``house_rules_audit.jsonl`` — append-only audit (secrets never written)

See ``docs/HOUSE_RULES.md`` and ``docs/SETUP.md``.
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic, write_text_atomic
from remedy.home import default_home

logger = logging.getLogger(__name__)

HOUSE_RULES_VERSION = 1
AUDIT_MAX_BYTES = 256 * 1024
AUDIT_KEEP_BYTES = 192 * 1024

# Status for the confirmed house-rules file.
STATUS_PENDING = "pending"
STATUS_OFFERED = "offered"
STATUS_COMPLETE = "complete"
STATUS_SKIPPED = "skipped"
STATUS_INHERITED = "inherited"

# Setup handoff modes. Conversation is the default once the mind is ready.
MODE_CONVERSATION = "conversation"
MODE_TRADITIONAL = "traditional"

# Origins that may change house rules. Stricter than OriginIsOwner: children,
# routines, hive daughters, messengers, and peer bots are all refused.
_OWNER_ORIGINS = frozenset({"", "desktop", "webui", "cli", "fixture"})

# Keys that must never appear in the audit log, draft files, or summaries.
SECRET_KEYS = frozenset(
    {
        "llm_api_key",
        "api_key",
        "provider_keys",
        "bot_token",
        "access_token",
        "app_token",
        "app_password",
        "app_secret",
        "verify_token",
        "signing_secret",
        "token",
        "password",
        "secret",
    }
)

# Settings that need an explicit yes before they change (default policy).
SENSITIVE_KEYS = frozenset(
    {
        "approval_mode",
        "trust_profile",
        "privacy_mode",
        "memory_encrypt",
        "inner_audit",
        "interop_inbound",
        "interop_bind",
        "interop_auto_reply",
        "interop_trust_policy",
        "access_scope",
        "connect_enabled",
        "connect_bind_host",
        "connect_bind_port",
        "claimidx_public_ledger",
    }
)

# Real [advanced] knobs defined in organism_knobs (interop from #72, muscle
# from #73). House rules write them to config.toml [advanced] so they bind
# immediately to the live table (resolve / normalizeAdvancedTable).
FORWARD_ADVANCED_KEYS = frozenset(
    {
        "interop_inbound",
        "interop_bind",
        "interop_a2a",
        "interop_mcp",
        "interop_auto_reply",
        "interop_audit",
        "interop_trust_policy",
        "muscle_gpu_policy",
        "muscle_route",
        "muscle_discover",
        "muscle_schedule",
        "mind_keep_gpu",
        "mind_handles_easy_chats",
    }
)

# Permission policies (advanced knob house_rules_permission).
PERM_CONFIRM_SENSITIVE = "confirm_sensitive"
PERM_CONFIRM_ALL = "confirm_all"
PERM_PLAIN = "plain"
PERM_LEVELS = (PERM_CONFIRM_SENSITIVE, PERM_CONFIRM_ALL, PERM_PLAIN)


@dataclass(frozen=True)
class Field:
    """One setting a house-rules question can set."""

    key: str
    default: Any
    kind: str = "str"  # str | bool | enum
    choices: tuple[str, ...] = ()
    config: str = "root"  # root | advanced
    sensitive: bool = False
    secret: bool = False


@dataclass(frozen=True)
class Question:
    """One skippable topic in the first-run conversation or form."""

    id: str
    title: str
    prompt: str
    fields: tuple[Field, ...]
    group: str = "house_rules"  # house_rules | setup


QUESTIONS: tuple[Question, ...] = (
    Question(
        "names",
        "Names",
        "What should I call you, and what should you call me? You can keep "
        "Remedy, or pick another name.",
        (
            Field("user_name", "", "str"),
            Field("name", "Remedy", "str"),
            Field(
                "agent_gender",
                "female",
                "enum",
                ("female", "male", "neutral"),
            ),
        ),
        "setup",
    ),
    Question(
        "language",
        "Language",
        "Which language should I use? The default is this computer's language, "
        "and I can follow what you type.",
        (
            Field("ui_language", "auto", "str"),
        ),
        "setup",
    ),
    Question(
        "tone",
        "Tone",
        "How should I talk — balanced, concise, detailed, or playful?",
        (
            Field(
                "persona",
                "default",
                "enum",
                ("default", "balanced", "efficient", "concise", "detailed", "verbose", "playful", "sarcastic", "minimal"),
            ),
            Field("sarcasm_mode", False, "bool"),
        ),
        "setup",
    ),
    Question(
        "provider",
        "Provider",
        "If you want a cloud model as muscle, which provider should I use? "
        "You can skip this and add a key later in Settings. I will not write "
        "API keys into logs or the house-rules file.",
        (
            Field("llm_provider", "demo", "str"),
            Field("llm_model", "", "str"),
            Field("llm_base_url", "", "str"),
            Field("custom_llm_name", "", "str"),
            Field("llm_api_key", "", "str", secret=True),
        ),
        "setup",
    ),
    Question(
        "workspace",
        "Workspace",
        "Is there a default project folder I should use for tools and files? "
        "You can leave this empty.",
        (Field("project_path", "", "str"),),
        "setup",
    ),
    Question(
        "proactive",
        "How proactive",
        "Should I reach out between visits, and keep my own drives going? "
        "Reaching out stays off unless you say yes.",
        (
            Field("proactive_reach_out", False, "bool", config="advanced"),
            Field("drives_enabled", True, "bool", config="advanced"),
        ),
    ),
    Question(
        "privacy",
        "Privacy and audit",
        "Should I keep a visible audit of inner work, and turn on privacy mode "
        "that strips personal details from provider traffic?",
        (
            Field("privacy_mode", False, "bool", sensitive=True),
            Field("inner_audit", True, "bool", config="advanced", sensitive=True),
        ),
    ),
    Question(
        "tokens",
        "Token saving",
        "I can handle easy chats and pictures here on this PC, and send the "
        "muscle a short description instead of the raw image. That saves your "
        "tokens. Want that?",
        (
            Field("always_send_images", False, "bool", config="advanced"),
            Field("vision_force_decode", False, "bool"),
            Field("mind_handles_easy_chats", True, "bool", config="advanced"),
            Field("muscle_route", "auto", "enum", ("auto", "mind", "rmb", "local", "frontier"), config="advanced"),
        ),
    ),
    Question(
        "gpu",
        "Local muscle and the GPU",
        "If a local muscle (a bigger model on this PC) is running, how should "
        "we share the GPU? Auto is fine for most machines. On a 12 GB card, "
        "yielding the mind to the CPU lets a 9B muscle run at full speed.",
        (
            Field(
                "muscle_gpu_policy",
                "auto",
                "enum",
                ("auto", "share", "yield_mind", "keep_mind"),
                config="advanced",
            ),
            Field("mind_keep_gpu", False, "bool", config="advanced"),
        ),
    ),
    Question(
        "consent",
        "Consent",
        "I will always ask before spending money, sending messages, or deleting "
        "things — that's recommended and already the rule. Should I also ask "
        "before other tools run, or use the usual in-project auto mode?",
        (
            Field(
                "approval_mode",
                "auto",
                "enum",
                ("ask", "auto", "full"),
                sensitive=True,
            ),
            Field(
                "trust_profile",
                "balanced",
                "enum",
                ("conservative", "balanced", "autonomous"),
                sensitive=True,
            ),
        ),
    ),
    Question(
        "interop",
        "Other bots",
        "Should other bots be able to talk to me (A2A or MCP), and should I "
        "answer them automatically? Both stay off until you turn them on. "
        "Inbound connections stay on this computer and need a token.",
        (
            Field("interop_inbound", False, "bool", config="advanced", sensitive=True),
            Field("interop_auto_reply", True, "bool", config="advanced", sensitive=True),
        ),
    ),
    Question(
        "updates",
        "Updates",
        "Updates happen in the app. Should I check for them automatically and "
        "let you know, or only when you ask? Installs always wait for you.",
        (
            Field("update_check", "auto", "enum", ("auto", "ask")),
        ),
        "setup",
    ),
    Question(
        "launch",
        "Start with the computer",
        "Should I start with this computer so I'm ready when you are?",
        (Field("launch_at_login", False, "bool"),),
        "setup",
    ),
)

QUESTION_BY_ID: dict[str, Question] = {q.id: q for q in QUESTIONS}
FIELD_BY_KEY: dict[str, Field] = {f.key: f for q in QUESTIONS for f in q.fields}

_LOCK = threading.RLock()

# Persona aliases used in the old wizard and in casual answers.
_PERSONA_ALIASES = {
    "balanced": "default",
    "efficient": "concise",
    "short": "concise",
    "brief": "concise",
    "thorough": "detailed",
    "long": "verbose",
    "fun": "playful",
    "casual": "playful",
    "witty": "sarcastic",
    "dry": "sarcastic",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _home(home: str | Path | None = None) -> Path:
    if home is None:
        return default_home()
    return Path(home).expanduser()


def rules_path(home: str | Path | None = None) -> Path:
    return _home(home) / "house_rules.toml"


def session_path(home: str | Path | None = None) -> Path:
    return _home(home) / "setup_session.json"


def pending_path(home: str | Path | None = None) -> Path:
    return _home(home) / "house_rules_pending.json"


def prev_path(home: str | Path | None = None) -> Path:
    return _home(home) / "house_rules_prev.json"


def audit_path(home: str | Path | None = None) -> Path:
    return _home(home) / "house_rules_audit.jsonl"


def can_change_house_rules(origin: str | None) -> bool:
    """True only for the owner's own surfaces (desktop, web UI, CLI)."""
    o = (origin or "").strip().lower()
    return o in _OWNER_ORIGINS


def is_secret_key(key: str) -> bool:
    k = (key or "").strip().lower()
    if k in SECRET_KEYS:
        return True
    return (
        k.endswith("_token")
        or k.endswith("_password")
        or k.endswith("_secret")
        or k.endswith("_key")
        or k.endswith("api_key")
    )


def is_sensitive_key(key: str) -> bool:
    return (key or "").strip() in SENSITIVE_KEYS or bool(
        FIELD_BY_KEY.get(key) and FIELD_BY_KEY[key].sensitive
    )


def _permission(cfg: dict[str, Any] | None = None) -> str:
    try:
        from remedy.core.organism_knobs import resolve

        raw = str(resolve("house_rules_permission", cfg) or PERM_CONFIRM_SENSITIVE)
    except Exception:
        raw = PERM_CONFIRM_SENSITIVE
    raw = raw.strip().lower()
    return raw if raw in PERM_LEVELS else PERM_CONFIRM_SENSITIVE


def _onboarding_enabled(cfg: dict[str, Any] | None = None) -> bool:
    try:
        from remedy.core.organism_knobs import resolve_bool

        return bool(resolve_bool("onboarding", cfg))
    except Exception:
        return True


def _onboarding_reask(cfg: dict[str, Any] | None = None) -> bool:
    try:
        from remedy.core.organism_knobs import resolve_bool

        return bool(resolve_bool("onboarding_reask", cfg))
    except Exception:
        return False


def _scrub(obj: Any) -> Any:
    """Drop secret keys from any nested dict before it is stored or logged."""
    if isinstance(obj, dict):
        return {
            k: ("***" if is_secret_key(str(k)) else _scrub(v))
            for k, v in obj.items()
            if not is_secret_key(str(k))
        }
    if isinstance(obj, list):
        return [_scrub(x) for x in obj]
    return obj


def defaults() -> dict[str, Any]:
    """Default answers for every house-rules field (no secrets)."""
    out: dict[str, Any] = {}
    for field_ in FIELD_BY_KEY.values():
        if field_.secret:
            continue
        out[field_.key] = field_.default
    return out


def questions_public() -> list[dict[str, Any]]:
    """Catalog of skippable questions for the UI and the mind."""
    rows = []
    for q in QUESTIONS:
        rows.append(
            {
                "id": q.id,
                "title": q.title,
                "prompt": q.prompt,
                "group": q.group,
                "skippable": True,
                "fields": [
                    {
                        "key": f.key,
                        "default": None if f.secret else f.default,
                        "kind": f.kind,
                        "choices": list(f.choices),
                        "sensitive": f.sensitive,
                        "secret": f.secret,
                    }
                    for f in q.fields
                ],
            }
        )
    return rows


# --- TOML (restricted writer / reader for house_rules.toml) -----------------


def _toml_escape(value: str) -> str:
    return (
        '"'
        + (
            value.replace("\\", "\\\\")
            .replace('"', '\\"')
            .replace("\n", "\\n")
            .replace("\r", "\\r")
            .replace("\t", "\\t")
        )
        + '"'
    )


def _toml_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, float):
        return repr(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_scalar(v) for v in value) + "]"
    if value is None:
        return '""'
    return _toml_escape(str(value))


def _dump_toml(data: dict[str, Any]) -> str:
    lines = [
        "# Remedy house rules — answers and provenance.",
        "# API keys are never stored here. See docs/HOUSE_RULES.md.",
        "",
    ]
    tables: list[tuple[str, dict[str, Any]]] = []
    for key, value in data.items():
        if isinstance(value, dict):
            tables.append((key, value))
            continue
        if value is None:
            continue
        lines.append(f"{key} = {_toml_scalar(value)}")
    for name, table in tables:
        lines.append("")
        lines.append(f"[{name}]")
        if not table:
            continue
        # Nested provenance tables: [provenance.key]
        nested = [(k, v) for k, v in table.items() if isinstance(v, dict)]
        scalars = [(k, v) for k, v in table.items() if not isinstance(v, dict)]
        for k, v in scalars:
            if v is None:
                continue
            lines.append(f"{k} = {_toml_scalar(v)}")
        for k, v in nested:
            lines.append("")
            lines.append(f"[{name}.{k}]")
            for ik, iv in v.items():
                if iv is None or isinstance(iv, dict):
                    continue
                lines.append(f"{ik} = {_toml_scalar(iv)}")
    lines.append("")
    return "\n".join(lines)


def _load_toml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import tomllib

        raw = path.read_text(encoding="utf-8")
        data = tomllib.loads(raw)
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("failed to read %s", path)
        return {}


def _load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:
        logger.exception("failed to read %s", path)
        return {}


def empty_rules() -> dict[str, Any]:
    return {
        "version": HOUSE_RULES_VERSION,
        "status": STATUS_PENDING,
        "offered_at": "",
        "completed_at": "",
        "source": "",
        "answers": {},
        "provenance": {},
        "skipped": [],
        "update_offer": "",
        "update_offer_id": "",
    }


def empty_session() -> dict[str, Any]:
    return {
        "version": HOUSE_RULES_VERSION,
        "mode": MODE_CONVERSATION,
        "status": STATUS_PENDING,
        "onboarding_pending": False,
        "mind_failure": None,
        "draft": {},
        "skipped": [],
        "offered_at": "",
        "completed_at": "",
        "source": "",
        "setup_session_id": "",
        "update_offer": "",
        "update_offer_id": "",
        "update_offer_at": "",
    }


def load_rules(home: str | Path | None = None) -> dict[str, Any]:
    with _LOCK:
        data = _load_toml(rules_path(home))
        out = empty_rules()
        if not data:
            return out
        out.update({k: data[k] for k in out if k in data and k not in ("answers", "provenance", "skipped")})
        answers = data.get("answers")
        if isinstance(answers, dict):
            out["answers"] = _scrub(answers)
        prov = data.get("provenance")
        if isinstance(prov, dict):
            out["provenance"] = _scrub(prov)
        skipped = data.get("skipped")
        if isinstance(skipped, list):
            out["skipped"] = [str(x) for x in skipped]
        return out


def load_session(home: str | Path | None = None) -> dict[str, Any]:
    with _LOCK:
        data = _load_json(session_path(home))
        out = empty_session()
        if not data:
            return out
        out.update({k: data[k] for k in out if k in data})
        draft = out.get("draft")
        if isinstance(draft, dict):
            out["draft"] = _scrub(draft)
        else:
            out["draft"] = {}
        if out.get("mode") not in (MODE_CONVERSATION, MODE_TRADITIONAL):
            out["mode"] = MODE_CONVERSATION
        return out


def save_rules(data: dict[str, Any], home: str | Path | None = None) -> Path:
    path = rules_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = empty_rules()
    payload.update(data)
    payload["answers"] = _scrub(payload.get("answers") or {})
    payload["provenance"] = _scrub(payload.get("provenance") or {})
    payload["version"] = HOUSE_RULES_VERSION
    with _LOCK:
        write_text_atomic(path, _dump_toml(payload), mode=0o600)
    return path


def save_session(data: dict[str, Any], home: str | Path | None = None) -> Path:
    path = session_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = empty_session()
    payload.update(data)
    payload["draft"] = _scrub(payload.get("draft") or {})
    payload["version"] = HOUSE_RULES_VERSION
    # Never persist a secret, even if a caller slipped one in.
    payload = _scrub(payload)
    with _LOCK:
        write_json_atomic(path, payload, mode=0o600)
    return path


def _append_audit(entry: dict[str, Any], home: str | Path | None = None) -> None:
    try:
        from remedy.core.organism_knobs import resolve_bool

        if not resolve_bool("house_rules_audit"):
            return
    except Exception:
        pass
    path = audit_path(home)
    path.parent.mkdir(parents=True, exist_ok=True)
    clean = _scrub(entry)
    clean.setdefault("ts", _now())
    line = json.dumps(clean, ensure_ascii=False, default=str)
    with _LOCK:
        with path.open("a", encoding="utf-8") as fh:
            fh.write(line + "\n")
        try:
            size = path.stat().st_size
        except OSError:
            return
        if size <= AUDIT_MAX_BYTES:
            return
        text = path.read_text(encoding="utf-8")
        # Keep the tail.
        keep = text[-AUDIT_KEEP_BYTES:]
        cut = keep.find("\n")
        if cut >= 0:
            keep = keep[cut + 1 :]
        write_text_atomic(path, keep if keep.endswith("\n") else keep + "\n", mode=0o600)


def load_pending(home: str | Path | None = None) -> dict[str, Any] | None:
    data = _load_json(pending_path(home))
    return _scrub(data) if data else None


def save_pending(data: dict[str, Any] | None, home: str | Path | None = None) -> None:
    path = pending_path(home)
    if data is None:
        with _LOCK:
            if path.is_file():
                try:
                    path.unlink()
                except OSError:
                    pass
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, _scrub(data), mode=0o600)


def _snapshot_prev(home: str | Path | None = None) -> None:
    """Save the current rules + relevant config for one-step rollback."""
    snap = {
        "ts": _now(),
        "rules": load_rules(home),
        "config": _config_snapshot(home),
    }
    write_json_atomic(prev_path(home), _scrub(snap), mode=0o600)


def _config_snapshot(home: str | Path | None = None) -> dict[str, Any]:
    cfg = _load_config(home)
    keys = set(FIELD_BY_KEY) | set(SENSITIVE_KEYS) | set(FORWARD_ADVANCED_KEYS)
    out: dict[str, Any] = {}
    advanced = cfg.get("advanced") if isinstance(cfg.get("advanced"), dict) else {}
    for key in keys:
        if is_secret_key(key):
            continue
        field_ = FIELD_BY_KEY.get(key)
        if field_ and field_.config == "advanced":
            if key in advanced:
                out[key] = advanced[key]
        elif key in cfg:
            out[key] = cfg[key]
        elif key in advanced:
            out[key] = advanced[key]
    return _scrub(out)


def _load_config(home: str | Path | None = None) -> dict[str, Any]:
    try:
        from remedy.interfaces.config import config_path_for_home, load_config

        path = config_path_for_home(_home(home))
        if not path.exists():
            return {}
        cfg = load_config(path)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


def _write_config(cfg: dict[str, Any], home: str | Path | None = None) -> None:
    from remedy.interfaces.api_support import _write_config as api_write
    from remedy.interfaces.config import config_path_for_home

    path = config_path_for_home(_home(home))
    path.parent.mkdir(parents=True, exist_ok=True)
    api_write(path, cfg)


def _store_secret(key: str, value: str, home: str | Path | None = None, provider: str | None = None) -> None:
    """Write an API key to the secure store. Never to house_rules or the audit."""
    if not str(value or "").strip():
        return
    if key != "llm_api_key":
        return
    try:
        from remedy.interfaces.config import set_provider_key

        prov = (provider or "").strip() or str(_load_config(home).get("llm_provider") or "openai")
        set_provider_key(prov, value, cfg={"home_dir": str(_home(home))})
    except Exception:
        logger.exception("failed to store provider key (value not logged)")


def apply_config_patch(
    patch: dict[str, Any],
    *,
    home: str | Path | None = None,
    origin: str = "desktop",
) -> dict[str, Any]:
    """Merge a scrubbed patch into config.toml (and [advanced] as needed).

    Secrets are routed to the secure store and omitted from the returned diff.
    """
    clean: dict[str, Any] = {}
    secrets: dict[str, Any] = {}
    for key, value in (patch or {}).items():
        if is_secret_key(key):
            secrets[key] = value
            continue
        clean[key] = value
    cfg = _load_config(home)
    if not cfg:
        cfg = {"home_dir": str(_home(home))}
    changed: dict[str, Any] = {}
    advanced = dict(cfg.get("advanced") or {}) if isinstance(cfg.get("advanced"), dict) else {}
    for key, value in clean.items():
        field_ = FIELD_BY_KEY.get(key)
        use_advanced = (field_ and field_.config == "advanced") or key in FORWARD_ADVANCED_KEYS
        if use_advanced:
            prev = advanced.get(key)
            if prev != value:
                changed[key] = {"from": prev, "to": value}
            advanced[key] = value
        else:
            prev = cfg.get(key)
            if prev != value:
                changed[key] = {"from": prev, "to": value}
            cfg[key] = value
    if advanced:
        cfg["advanced"] = advanced
    _write_config(cfg, home)
    provider = str(clean.get("llm_provider") or cfg.get("llm_provider") or "")
    for skey, sval in secrets.items():
        _store_secret(str(skey), str(sval or ""), home, provider)
        changed[str(skey)] = {"from": None, "to": "stored"}
    return changed


def normalize_value(key: str, raw: Any) -> Any:
    """Coerce a spoken or form answer onto the field's default/choices."""
    field_ = FIELD_BY_KEY.get(key)
    if field_ is None:
        return raw
    if field_.secret:
        return "" if raw is None else str(raw)
    if field_.kind == "bool":
        if isinstance(raw, bool):
            return raw
        s = str(raw or "").strip().lower()
        if s in ("1", "true", "yes", "on", "y"):
            return True
        if s in ("0", "false", "no", "off", "n", ""):
            return False
        return bool(field_.default)
    if field_.kind == "enum" or field_.choices:
        s = str(raw if raw is not None else field_.default).strip().lower()
        if key == "persona":
            s = _PERSONA_ALIASES.get(s, s)
            # Keep "balanced" as itself if stored that way; default maps to default.
            if s == "balanced":
                s = "default"
        if field_.choices and s not in field_.choices:
            return field_.default
        return s
    if raw is None:
        return field_.default
    return str(raw).strip()


def normalize_patch(raw: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for key, value in (raw or {}).items():
        if key not in FIELD_BY_KEY and key not in SENSITIVE_KEYS and key not in FORWARD_ADVANCED_KEYS:
            continue
        if is_secret_key(key):
            # Keep only long enough to store; callers must not log it.
            if str(value or "").strip():
                out[key] = value
            continue
        out[key] = normalize_value(key, value)
    return out


# --- First-run offer / draft / mode -----------------------------------------


def mark_onboarding_pending(home: str | Path | None = None) -> dict[str, Any]:
    """Called when setup is first marked complete-or-ready to start house rules.

    Existing installs never get this flag, so they are not nagged on upgrade.
    """
    session = load_session(home)
    if session.get("status") in (STATUS_COMPLETE, STATUS_SKIPPED, STATUS_INHERITED):
        return session
    session["onboarding_pending"] = True
    session["status"] = STATUS_PENDING
    session["source"] = session.get("source") or "setup"
    save_session(session, home)
    rules = load_rules(home)
    if rules.get("status") in (STATUS_COMPLETE, STATUS_SKIPPED, STATUS_INHERITED):
        return session
    rules["status"] = STATUS_PENDING
    save_rules(rules, home)
    return session


def take_onboarding_offer(home: str | Path | None = None) -> bool:
    """True exactly once when the first-run house-rules prompt should be injected.

    Marks the offer as made even if the model never voices it — no second nag.
    ``onboarding_reask`` (off by default) allows one more auto-offer if the
    first was ignored (status=offered) and never completed or skipped.
    """
    if not _onboarding_enabled():
        return False
    with _LOCK:
        session = load_session(home)
        rules = load_rules(home)
        status = str(rules.get("status") or session.get("status") or "")
        if status in (STATUS_COMPLETE, STATUS_SKIPPED, STATUS_INHERITED):
            return False
        # Only auto-offer when first-run setup marked onboarding_pending.
        # empty_rules defaults status to pending, so status alone is not enough.
        if status == STATUS_OFFERED:
            if not _onboarding_reask() or session.get("reasked"):
                return False
            session["reasked"] = True
        elif not session.get("onboarding_pending"):
            return False
        now = _now()
        rules["status"] = STATUS_OFFERED
        rules["offered_at"] = rules.get("offered_at") or now
        session["status"] = STATUS_OFFERED
        session["offered_at"] = session.get("offered_at") or now
        save_rules(rules, home)
        save_session(session, home)
        _append_audit(
            {"action": "offer", "actor": "owner", "source": "onboarding"},
            home,
        )
        return True


def offer_prompt() -> str:
    """Short standing instruction for the mind when the first-run offer fires."""
    topics = "; ".join(f"{q.title} ({q.id})" for q in QUESTIONS)
    return (
        "House rules (one-time, optional, first run): Welcome them briefly and "
        "walk through house rules in a short, friendly conversation. Ask one "
        "topic at a time. Every question is skippable; they can also skip all "
        "and keep the defaults. After answers or skips, show a short summary "
        "and ask them to confirm. Use the house_rules tool (record, skip_all, "
        "summarize, confirm). Never nag if they skip or ignore this. Peers "
        "cannot set house rules. They can switch to Traditional setup at any "
        "time; answers already given are kept. Topics: " + topics + "."
    )


# One-shot pop-up for existing installs after this update. Never a nag:
# Let's go or Not now, and it does not appear again.
UPDATE_OFFER_ID = "major-changes-2026-11"
UPDATE_OFFER_TITLE = "House rules"
UPDATE_OFFER_BODY = (
    "Remedy has changed in some big ways: a new built-in mind, smarter token "
    "saving, local muscle sharing, and talking with your other bots. It's worth "
    "a few minutes to go over the house rules together."
)
UPDATE_OFFER_PRIMARY = "Let's go"
UPDATE_OFFER_SECONDARY = "Not now"


def update_offer_available(home: str | Path | None = None) -> bool:
    """True once, for an already-set-up install that has not yet had this pop-up."""
    if not _onboarding_enabled():
        return False
    if not bool(_load_config(home).get("setup_completed")):
        return False
    session = load_session(home)
    rules = load_rules(home)
    seen = str(session.get("update_offer") or rules.get("update_offer") or "")
    if seen in ("accepted", "declined"):
        return False
    status = str(rules.get("status") or session.get("status") or "")
    # They already finished or skipped the house-rules conversation itself.
    if status in (STATUS_COMPLETE, STATUS_SKIPPED):
        return False
    source = str(session.get("source") or rules.get("source") or "")
    if session.get("onboarding_pending") and source != "revisit":
        return False
    return True


def update_offer_public(home: str | Path | None = None) -> dict[str, Any]:
    """Copy and availability for the desktop pop-up."""
    return {
        "available": update_offer_available(home),
        "id": UPDATE_OFFER_ID,
        "title": UPDATE_OFFER_TITLE,
        "body": UPDATE_OFFER_BODY,
        "primary": UPDATE_OFFER_PRIMARY,
        "secondary": UPDATE_OFFER_SECONDARY,
    }


def resolve_update_offer(
    choice: str,
    *,
    home: str | Path | None = None,
    origin: str = "desktop",
) -> dict[str, Any]:
    """Record Let's go or Not now. The pop-up never shows again after either."""
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    session = load_session(home)
    rules = load_rules(home)
    already = str(session.get("update_offer") or rules.get("update_offer") or "")
    if already in ("accepted", "declined"):
        return {
            "ok": True,
            "choice": already,
            "already": True,
            "update_offer": update_offer_public(home),
        }
    raw = (choice or "").strip().lower().replace(" ", "_").replace("'", "")
    accepted = raw in ("go", "accepted", "lets_go", "letsgo", "update_offer_go")
    recorded = "accepted" if accepted else "declined"
    now = _now()
    session["update_offer"] = recorded
    session["update_offer_id"] = UPDATE_OFFER_ID
    session["update_offer_at"] = now
    rules["update_offer"] = recorded
    rules["update_offer_id"] = UPDATE_OFFER_ID
    save_session(session, home)
    save_rules(rules, home)
    _append_audit(
        {
            "action": "update_offer",
            "actor": "owner",
            "origin": origin,
            "choice": recorded,
            "id": UPDATE_OFFER_ID,
        },
        home,
    )
    out: dict[str, Any] = {
        "ok": True,
        "choice": recorded,
        "update_offer": update_offer_public(home),
    }
    if accepted:
        out.update(revisit(home=home, origin=origin))
        # revisit reloads and saves the session — restore the consumed flag.
        session = load_session(home)
        rules = load_rules(home)
        session["update_offer"] = "accepted"
        session["update_offer_id"] = UPDATE_OFFER_ID
        session["update_offer_at"] = now
        rules["update_offer"] = "accepted"
        rules["update_offer_id"] = UPDATE_OFFER_ID
        save_session(session, home)
        save_rules(rules, home)
        out["update_offer"] = update_offer_public(home)
        out["session"] = public_session(home)
    return out


def revisit_prompt() -> str:
    return (
        "The owner asked to go over house rules again. Walk through the same "
        "skippable topics, one at a time, show a short summary, and confirm "
        "before applying. Use the house_rules tool. Skipped items keep their "
        "current value (or the default if they were never set)."
    )


def set_mode(mode: str, home: str | Path | None = None) -> dict[str, Any]:
    """Switch between conversation and traditional setup without losing the draft."""
    wanted = MODE_TRADITIONAL if str(mode).strip().lower() == MODE_TRADITIONAL else MODE_CONVERSATION
    session = load_session(home)
    session["mode"] = wanted
    save_session(session, home)
    return session


def update_draft(
    patch: dict[str, Any],
    *,
    skipped: list[str] | None = None,
    home: str | Path | None = None,
    origin: str = "desktop",
) -> dict[str, Any]:
    """Merge answers into the shared draft (conversation and form share this)."""
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    session = load_session(home)
    draft = dict(session.get("draft") or {})
    normalized = normalize_patch(patch)
    # Secrets: store immediately, remember only that they were set.
    secrets_set = list(session.get("secrets_set") or [])
    stored_patch = dict(normalized)
    for key in list(stored_patch):
        if is_secret_key(key):
            _store_secret(key, str(stored_patch.pop(key) or ""), home, str(stored_patch.get("llm_provider") or draft.get("llm_provider") or ""))
            if key not in secrets_set:
                secrets_set.append(key)
    draft.update(_scrub(stored_patch))
    session["draft"] = draft
    session["secrets_set"] = secrets_set
    if skipped:
        have = list(session.get("skipped") or [])
        for item in skipped:
            if item not in have:
                have.append(item)
        session["skipped"] = have
    if session.get("status") == STATUS_PENDING:
        session["status"] = "in_progress"
    save_session(session, home)
    return {"ok": True, "session": public_session(home)}


def record_answers(
    answers: dict[str, Any],
    *,
    skipped: list[str] | None = None,
    home: str | Path | None = None,
    origin: str = "desktop",
    source: str = "onboarding",
) -> dict[str, Any]:
    """Record answers during onboarding (also updates the shared draft)."""
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    result = update_draft(answers, skipped=skipped, home=home, origin=origin)
    if not result.get("ok"):
        return result
    _append_audit(
        {
            "action": "record",
            "actor": "owner",
            "origin": origin,
            "source": source,
            "keys": sorted(_scrub(normalize_patch(answers)).keys()),
            "skipped": list(skipped or []),
        },
        home,
    )
    return result


def skip_all(
    *,
    home: str | Path | None = None,
    origin: str = "desktop",
    source: str = "skip",
) -> dict[str, Any]:
    """Use defaults for every unanswered question and mark setup complete."""
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    session = load_session(home)
    draft = dict(session.get("draft") or {})
    applied = dict(defaults())
    applied.update(draft)  # keep anything already answered
    return _finalize(
        applied,
        skipped=[q.id for q in QUESTIONS],
        home=home,
        origin=origin,
        source=source,
        status=STATUS_SKIPPED,
    )


def summary_lines(
    answers: dict[str, Any] | None = None,
    *,
    skipped: list[str] | None = None,
    home: str | Path | None = None,
) -> list[str]:
    """Human-readable summary of the draft (or given answers) vs defaults."""
    session = load_session(home)
    rules = load_rules(home)
    values = dict(defaults())
    values.update(rules.get("answers") or {})
    values.update(session.get("draft") or {})
    if answers:
        values.update(_scrub(normalize_patch(answers)))
    skip = set(skipped or session.get("skipped") or rules.get("skipped") or [])
    lines = []
    for q in QUESTIONS:
        field_bits = []
        any_set = False
        for f in q.fields:
            if f.secret:
                set_flag = f.key in (session.get("secrets_set") or [])
                field_bits.append(f"{f.key}: {'saved in the secure store' if set_flag else 'not set'}")
                any_set = any_set or set_flag
                continue
            val = values.get(f.key, f.default)
            flag = "default" if val == f.default else "set"
            if q.id in skip and val == f.default:
                flag = "skipped (default)"
            if val != f.default:
                any_set = True
            field_bits.append(f"{f.key}: {val} ({flag})")
        mark = "skipped" if q.id in skip and not any_set else "ok"
        lines.append(f"{q.title}: " + "; ".join(field_bits) + f" [{mark}]")
    return lines


def summarize(*, home: str | Path | None = None) -> dict[str, Any]:
    lines = summary_lines(home=home)
    return {"ok": True, "summary": "\n".join(lines), "lines": lines, "session": public_session(home)}


def confirm_onboarding(
    *,
    home: str | Path | None = None,
    origin: str = "desktop",
    source: str = "onboarding",
) -> dict[str, Any]:
    """Apply the draft (defaults for anything skipped) after the owner confirms."""
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    session = load_session(home)
    applied = dict(defaults())
    applied.update(session.get("draft") or {})
    skipped = list(session.get("skipped") or [])
    return _finalize(
        applied,
        skipped=skipped,
        home=home,
        origin=origin,
        source=source,
        status=STATUS_COMPLETE,
    )


def _finalize(
    answers: dict[str, Any],
    *,
    skipped: list[str],
    home: str | Path | None,
    origin: str,
    source: str,
    status: str,
) -> dict[str, Any]:
    _snapshot_prev(home)
    clean = _scrub(normalize_patch(answers))
    # Defaults fill gaps.
    filled = dict(defaults())
    filled.update(clean)
    changed = apply_config_patch(filled, home=home, origin=origin)
    now = _now()
    rules = load_rules(home)
    prov = dict(rules.get("provenance") or {})
    for key, value in filled.items():
        if is_secret_key(key):
            continue
        prov[key] = {
            "set_by": "owner",
            "set_at": now,
            "source": "skip" if key in set(skipped) and value == FIELD_BY_KEY.get(key, Field(key, None)).default else source,
            "skipped": key in set(skipped) and value == defaults().get(key),
        }
    rules["answers"] = filled
    rules["provenance"] = prov
    rules["skipped"] = list(skipped)
    rules["status"] = status
    rules["completed_at"] = now
    rules["source"] = source
    rules["update_offer"] = "accepted"
    rules["update_offer_id"] = UPDATE_OFFER_ID
    save_rules(rules, home)
    session = load_session(home)
    session["draft"] = filled
    session["skipped"] = list(skipped)
    session["status"] = status
    session["completed_at"] = now
    session["onboarding_pending"] = False
    session["source"] = source
    session["update_offer"] = "accepted"
    session["update_offer_id"] = UPDATE_OFFER_ID
    save_session(session, home)
    try:
        from remedy.interfaces.config import mark_setup_completed

        mark_setup_completed(home_dir=_home(home), extra={"setup_completed": True})
    except Exception:
        # Still write the flag ourselves if the helper is mid-recursion.
        cfg = _load_config(home)
        cfg["setup_completed"] = True
        _write_config(cfg, home)
    _append_audit(
        {
            "action": "finalize",
            "actor": "owner",
            "origin": origin,
            "source": source,
            "status": status,
            "changes": changed,
            "skipped": list(skipped),
        },
        home,
    )
    return {
        "ok": True,
        "status": status,
        "summary": "\n".join(summary_lines(filled, skipped=skipped, home=home)),
        "changes": _scrub(changed),
        "session": public_session(home),
    }


# --- Talk-to-change ---------------------------------------------------------


def needs_confirmation(keys: list[str], cfg: dict[str, Any] | None = None) -> bool:
    level = _permission(cfg)
    if level == PERM_CONFIRM_ALL:
        return True
    # PERM_PLAIN still confirms sensitive keys.
    return any(is_sensitive_key(k) for k in keys)


def propose(
    patch: dict[str, Any],
    *,
    home: str | Path | None = None,
    origin: str = "desktop",
    confirm: bool = False,
    source: str = "chat",
) -> dict[str, Any]:
    """Propose a settings change from chat. Sensitive keys wait for confirm."""
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    normalized = normalize_patch(patch)
    visible = _scrub(normalized)
    if not visible and not any(is_secret_key(k) for k in normalized):
        return {"ok": False, "error": "no recognized house-rules keys in the patch"}
    keys = list(visible.keys()) + [k for k in normalized if is_secret_key(k)]
    wait = needs_confirmation(keys) and not confirm
    diff = _diff_against_current(visible, home)
    if wait:
        pending = {
            "id": f"hr-{int(datetime.now(timezone.utc).timestamp())}",
            "created_at": _now(),
            "kind": "change",
            "keys": keys,
            "patch": visible,
            "diff": diff,
            "source": source,
            "origin": origin,
        }
        # Secrets in the pending patch: store them only at apply time. If a
        # secret was proposed, remember a flag, not the value.
        save_pending(pending, home)
        _append_audit(
            {
                "action": "propose",
                "actor": "owner",
                "origin": origin,
                "source": source,
                "keys": keys,
                "needs_confirmation": True,
            },
            home,
        )
        return {
            "ok": True,
            "needs_confirmation": True,
            "pending_id": pending["id"],
            "diff": diff,
            "summary": _format_diff(diff),
            "message": "This change needs your OK. Say yes to apply it, or no to cancel.",
        }
    return _apply_change(normalized, home=home, origin=origin, source=source)


def confirm_pending(
    *,
    home: str | Path | None = None,
    origin: str = "desktop",
    pending_id: str | None = None,
) -> dict[str, Any]:
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    pending = load_pending(home)
    if not pending:
        return {"ok": False, "error": "no pending house-rules change"}
    if pending_id and pending.get("id") != pending_id:
        return {"ok": False, "error": "pending change id does not match"}
    if pending.get("kind") == "onboarding_confirm":
        save_pending(None, home)
        return confirm_onboarding(home=home, origin=origin, source="onboarding")
    patch = dict(pending.get("patch") or {})
    save_pending(None, home)
    return _apply_change(patch, home=home, origin=origin, source=str(pending.get("source") or "chat"))


def decline_pending(
    *,
    home: str | Path | None = None,
    origin: str = "desktop",
) -> dict[str, Any]:
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    pending = load_pending(home)
    save_pending(None, home)
    _append_audit({"action": "decline", "actor": "owner", "origin": origin}, home)
    return {"ok": True, "declined": bool(pending), "pending_id": (pending or {}).get("id")}


def rollback(
    *,
    home: str | Path | None = None,
    origin: str = "desktop",
) -> dict[str, Any]:
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    prev = _load_json(prev_path(home))
    if not prev:
        return {"ok": False, "error": "no previous house-rules snapshot to roll back to"}
    # Snapshot current as the new previous so rollback itself can be undone.
    current = {
        "ts": _now(),
        "rules": load_rules(home),
        "config": _config_snapshot(home),
    }
    rules = prev.get("rules") if isinstance(prev.get("rules"), dict) else empty_rules()
    config_bits = prev.get("config") if isinstance(prev.get("config"), dict) else {}
    _snapshot_prev(home)  # will be overwritten; write current explicitly below
    write_json_atomic(prev_path(home), _scrub(current), mode=0o600)
    save_rules(rules, home)
    apply_config_patch(_scrub(config_bits), home=home, origin=origin)
    _append_audit({"action": "rollback", "actor": "owner", "origin": origin}, home)
    return {"ok": True, "rules": load_rules(home), "summary": "\n".join(summary_lines(home=home))}


def revisit(
    *,
    home: str | Path | None = None,
    origin: str = "desktop",
) -> dict[str, Any]:
    """Start the house-rules conversation again, keeping current values as the draft."""
    if not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    session = load_session(home)
    rules = load_rules(home)
    draft = dict(defaults())
    draft.update(rules.get("answers") or {})
    draft.update(session.get("draft") or {})
    session["draft"] = _scrub(draft)
    session["mode"] = MODE_CONVERSATION
    session["onboarding_pending"] = True
    session["status"] = STATUS_PENDING
    session["source"] = "revisit"
    save_session(session, home)
    rules["status"] = STATUS_PENDING
    save_rules(rules, home)
    _append_audit({"action": "revisit", "actor": "owner", "origin": origin}, home)
    return {
        "ok": True,
        "prompt": revisit_prompt(),
        "session": public_session(home),
        "questions": questions_public(),
    }


def _diff_against_current(patch: dict[str, Any], home: str | Path | None) -> dict[str, Any]:
    current = dict(defaults())
    current.update(load_rules(home).get("answers") or {})
    current.update(_config_snapshot(home))
    diff = {}
    for key, value in patch.items():
        if is_secret_key(key):
            continue
        old = current.get(key)
        if old != value:
            diff[key] = {"from": old, "to": value}
    return diff


def _format_diff(diff: dict[str, Any]) -> str:
    if not diff:
        return "No changes."
    lines = []
    for key, pair in diff.items():
        lines.append(f"{key}: {pair.get('from')!r} → {pair.get('to')!r}")
    return "\n".join(lines)


def _apply_change(
    patch: dict[str, Any],
    *,
    home: str | Path | None,
    origin: str,
    source: str,
) -> dict[str, Any]:
    _snapshot_prev(home)
    clean = normalize_patch(patch)
    changed = apply_config_patch(clean, home=home, origin=origin)
    now = _now()
    rules = load_rules(home)
    answers = dict(rules.get("answers") or {})
    prov = dict(rules.get("provenance") or {})
    visible = _scrub(clean)
    answers.update(visible)
    for key in visible:
        prov[key] = {"set_by": "owner", "set_at": now, "source": source, "skipped": False}
    rules["answers"] = answers
    rules["provenance"] = prov
    if rules.get("status") not in (STATUS_COMPLETE, STATUS_SKIPPED, STATUS_INHERITED):
        rules["status"] = STATUS_COMPLETE
        rules["completed_at"] = now
    save_rules(rules, home)
    session = load_session(home)
    draft = dict(session.get("draft") or {})
    draft.update(visible)
    session["draft"] = draft
    save_session(session, home)
    save_pending(None, home)
    _append_audit(
        {
            "action": "apply",
            "actor": "owner",
            "origin": origin,
            "source": source,
            "changes": changed,
        },
        home,
    )
    return {
        "ok": True,
        "needs_confirmation": False,
        "changes": _scrub(changed),
        "diff": _diff_against_current(visible, home) if False else _scrub(changed),
        "summary": _format_diff(changed),
        "session": public_session(home),
    }


# --- Mind gate / public setup snapshot --------------------------------------


def classify_mind_failure(status: dict[str, Any] | None) -> dict[str, Any] | None:
    """Return a {reason, detail} if the mind cannot start, else None.

    No GPU is not a failure — the mind can run on CPU. Failed downloads,
    missing files after a failed verify, or clearly insufficient RAM are.
    """
    if not isinstance(status, dict):
        return None
    progress = status.get("progress") if isinstance(status.get("progress"), dict) else {}
    err = str(progress.get("error") or status.get("error") or "").strip()
    health = status.get("health") if isinstance(status.get("health"), dict) else {}
    ram = health.get("ram_gb")
    min_ram = health.get("min_ram_gb")
    phase = str(progress.get("phase") or "").strip().lower()
    if err:
        low = err.lower()
        reason = "download"
        if "verify" in low or "sha" in low or "checksum" in low or "hash" in low:
            reason = "verify"
        elif "memory" in low or "ram" in low or "oom" in low:
            reason = "low_ram"
        return {"reason": reason, "detail": err, "at": _now()}
    if phase in ("failed", "error"):
        return {"reason": "download", "detail": err or "The mind download failed.", "at": _now()}
    if isinstance(ram, (int, float)) and isinstance(min_ram, (int, float)) and ram < min_ram:
        return {
            "reason": "low_ram",
            "detail": f"This computer has about {ram:.1f} GB of RAM; the mind wants at least {min_ram:.1f} GB.",
            "at": _now(),
        }
    return None


def note_mind_failure(
    failure: dict[str, Any] | None,
    home: str | Path | None = None,
) -> dict[str, Any]:
    session = load_session(home)
    session["mind_failure"] = _scrub(failure) if failure else None
    if failure and session.get("mode") == MODE_CONVERSATION:
        # Suggest traditional; do not force-switch if they already chose.
        session["fallback_suggested"] = True
    save_session(session, home)
    return session


def mind_ready(status: dict[str, Any] | None) -> bool:
    if not isinstance(status, dict):
        return False
    if classify_mind_failure(status):
        return False
    if status.get("installed") and (status.get("ready") or status.get("running")):
        return True
    # Files present is enough to start a conversation; the host can autostart.
    return bool(status.get("installed"))


def public_session(
    home: str | Path | None = None,
    *,
    mind_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Scrubbed snapshot for GET /api/setup and the desktop setup flow."""
    session = load_session(home)
    rules = load_rules(home)
    failure = session.get("mind_failure")
    if mind_status is not None:
        ready = mind_ready(mind_status)
        live_fail = classify_mind_failure(mind_status)
        if live_fail:
            failure = live_fail
            note_mind_failure(live_fail, home)
            session = load_session(home)
        progress = mind_status.get("progress") if isinstance(mind_status.get("progress"), dict) else {}
    else:
        ready = False
        progress = {}
    pending = load_pending(home)
    return {
        "version": HOUSE_RULES_VERSION,
        "mode": session.get("mode") or MODE_CONVERSATION,
        "status": session.get("status") or rules.get("status") or STATUS_PENDING,
        "rules_status": rules.get("status"),
        "onboarding_pending": bool(session.get("onboarding_pending")),
        "onboarding_enabled": _onboarding_enabled(),
        "mind_ready": bool(ready),
        "mind_failure": failure,
        "mind_progress": {
            "phase": progress.get("phase"),
            "message": progress.get("message"),
            "bytes_done": progress.get("bytes_done"),
            "bytes_total": progress.get("bytes_total"),
            "error": progress.get("error"),
            "cancellable": progress.get("cancellable"),
        } if progress else None,
        "draft": _scrub(session.get("draft") or {}),
        "skipped": list(session.get("skipped") or []),
        "secrets_set": list(session.get("secrets_set") or []),
        "answers": _scrub(rules.get("answers") or {}),
        "pending": pending,
        "setup_session_id": session.get("setup_session_id") or "",
        "questions": questions_public(),
        "defaults": defaults(),
        "summary": "\n".join(summary_lines(home=home)),
        "offer_prompt": offer_prompt(),
        "update_offer": update_offer_public(home),
    }


def bind_setup_session_id(session_id: str, home: str | Path | None = None) -> dict[str, Any]:
    session = load_session(home)
    session["setup_session_id"] = str(session_id or "").strip()
    save_session(session, home)
    return session


def close_onboarding_if_still_open(home: str | Path | None = None) -> None:
    """When setup_completed is written without a house-rules finish (CLI
    ``--skip-setup``, or an older skip path), keep defaults and never nag."""
    rules = load_rules(home)
    status = str(rules.get("status") or "")
    if status in (STATUS_COMPLETE, STATUS_SKIPPED, STATUS_INHERITED):
        return
    session = load_session(home)
    if session.get("onboarding_pending") and status in (STATUS_PENDING, STATUS_OFFERED, ""):
        # A first-run setup flow is still in progress — don't close it just
        # because config was created.
        if not _load_config(home).get("setup_completed"):
            return
        # setup_completed is already true and house rules never finished.
    applied = dict(defaults())
    applied.update(session.get("draft") or {})
    applied.update(rules.get("answers") or {})
    now = _now()
    rules["answers"] = _scrub(applied)
    rules["status"] = STATUS_INHERITED
    rules["completed_at"] = now
    rules["source"] = "setup_skipped"
    save_rules(rules, home)
    session["draft"] = _scrub(applied)
    session["status"] = STATUS_INHERITED
    session["onboarding_pending"] = False
    session["completed_at"] = now
    session["source"] = "setup_skipped"
    save_session(session, home)
    _append_audit({"action": "inherit", "source": "setup_skipped"}, home)


# --- Tool dispatch (Python worker + tests) ----------------------------------


_MUTATING = frozenset(
    {
        "record", "skip_all", "confirm", "propose", "decline", "rollback",
        "revisit", "set_mode", "finalize",
        "update_offer_go", "update_offer_later", "update_offer_accept", "update_offer_dismiss",
    }
)


def dispatch(inp: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one ``house_rules`` / ``setup.session`` action.

    ``caller_origin`` must be injected by the runtime. Missing origin is
    treated as the owner (desktop/CLI tests and unbound unit calls).
    """
    data = dict(inp or {})
    origin = str(data.get("caller_origin") or "").strip()
    action = str(data.get("action") or "status").strip().lower()
    home = data.get("home_dir") or None
    if action in _MUTATING and not can_change_house_rules(origin):
        return {"ok": False, "error": "peers cannot change house rules"}
    try:
        if action in ("status", "get", ""):
            return {"ok": True, **public_session(home)}
        if action == "summarize":
            return summarize(home=home)
        if action == "record":
            answers = data.get("answers") or data.get("patch") or {}
            skipped = data.get("skipped") or []
            if not isinstance(answers, dict):
                return {"ok": False, "error": "answers must be an object"}
            return record_answers(answers, skipped=list(skipped) if isinstance(skipped, list) else [], home=home, origin=origin)
        if action == "skip_all":
            return skip_all(home=home, origin=origin)
        if action == "confirm":
            if load_pending(home):
                return confirm_pending(home=home, origin=origin, pending_id=data.get("pending_id"))
            return confirm_onboarding(home=home, origin=origin)
        if action == "propose":
            patch = data.get("patch") or data.get("answers") or {}
            if not isinstance(patch, dict):
                return {"ok": False, "error": "patch must be an object"}
            return propose(
                patch,
                home=home,
                origin=origin,
                confirm=bool(data.get("confirm")),
                source=str(data.get("source") or "chat"),
            )
        if action == "decline":
            return decline_pending(home=home, origin=origin)
        if action == "rollback":
            return rollback(home=home, origin=origin)
        if action == "revisit":
            return revisit(home=home, origin=origin)
        if action == "set_mode":
            set_mode(str(data.get("mode") or ""), home)
            return {"ok": True, "session": public_session(home)}
        if action == "update_draft":
            patch = data.get("patch") or data.get("answers") or {}
            if not isinstance(patch, dict):
                return {"ok": False, "error": "patch must be an object"}
            skipped = data.get("skipped") if isinstance(data.get("skipped"), list) else None
            return update_draft(patch, skipped=skipped, home=home, origin=origin)
        if action == "offer_prompt":
            return {"ok": True, "prompt": offer_prompt()}
        if action in ("update_offer_go", "update_offer_later", "update_offer_accept", "update_offer_dismiss"):
            return resolve_update_offer(action, home=home, origin=origin)
        return {"ok": False, "error": f"unknown action {action}"}
    except Exception as exc:
        logger.exception("house_rules.dispatch failed")
        return {"ok": False, "error": "house rules action failed"}
