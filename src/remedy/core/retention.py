"""Data retention policies for local owner data (sessions, attachments, shots, undo).

Default: no aggressive wipe (owner power). When configured, idle prune runs at
serve startup and on demand via API/tool.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Sensible clamps (days). 0 = disabled for that category.
_MIN_DAYS = 0
_MAX_DAYS = 3650


@dataclass
class RetentionPolicy:
    """Days to keep; 0 means never auto-purge that category."""

    session_days: int = 0
    attachment_days: int = 0
    computer_shot_days: int = 14  # soft default: drop stale CUA screenshots
    undo_days: int = 30
    log_days: int = 30

    @classmethod
    def from_config(cls, cfg: dict[str, Any] | None) -> RetentionPolicy:
        raw = cfg if isinstance(cfg, dict) else {}
        nested = raw.get("retention") if isinstance(raw.get("retention"), dict) else {}

        def _days(*keys: str, default: int) -> int:
            """First present key wins (nested then flat). 0 is a valid disable."""
            for key in keys:
                for src in (nested, raw):
                    if key in src and src.get(key) is not None:
                        try:
                            n = int(src.get(key))
                        except (TypeError, ValueError):
                            n = default
                        return max(_MIN_DAYS, min(_MAX_DAYS, n))
            return default

        return cls(
            session_days=_days("session_days", "retention_session_days", default=0),
            attachment_days=_days(
                "attachment_days", "retention_attachment_days", default=0
            ),
            computer_shot_days=_days(
                "computer_shot_days", "retention_computer_shot_days", default=14
            ),
            undo_days=_days("undo_days", "retention_undo_days", default=30),
            log_days=_days("log_days", "retention_log_days", default=30),
        )


def _home_from_cfg(cfg: dict[str, Any] | None) -> Path:
    if isinstance(cfg, dict) and cfg.get("home_dir"):
        return Path(str(cfg["home_dir"])).expanduser().resolve()
    return (Path.home() / ".remedy").expanduser().resolve()


def _purge_dir_by_mtime(dir_path: Path, *, max_age_days: int, patterns: tuple[str, ...] = ("*",)) -> int:
    if max_age_days <= 0 or not dir_path.is_dir():
        return 0
    cutoff = time.time() - (max_age_days * 86400)
    removed = 0
    for pat in patterns:
        for p in dir_path.glob(pat):
            try:
                if not p.is_file():
                    continue
                if p.stat().st_mtime < cutoff:
                    p.unlink(missing_ok=True)
                    removed += 1
            except OSError as exc:
                logger.debug("retention skip %s: %s", p, exc)
    return removed


def purge_attachments(home: Path, *, max_age_days: int) -> int:
    return _purge_dir_by_mtime(home / "attachments", max_age_days=max_age_days)


def purge_computer_shots(home: Path, *, max_age_days: int) -> int:
    base = home / "computer"
    n = 0
    for sub in ("shots", "screenshots", "captures"):
        n += _purge_dir_by_mtime(base / sub, max_age_days=max_age_days)
    # Flat files under computer/
    n += _purge_dir_by_mtime(
        base,
        max_age_days=max_age_days,
        patterns=("*.png", "*.jpg", "*.jpeg", "*.webp"),
    )
    return n


def purge_undo(home: Path, *, max_age_days: int) -> int:
    return _purge_dir_by_mtime(
        home / "undo",
        max_age_days=max_age_days,
        patterns=("*.jsonl", "*.json"),
    )


def purge_logs(home: Path, *, max_age_days: int) -> int:
    return _purge_dir_by_mtime(
        home / "logs",
        max_age_days=max_age_days,
        patterns=("*.log", "*.log.*", "*.gz"),
    )


def purge_old_sessions(store: Any, *, max_age_days: int) -> int:
    """Delete chat sessions older than *max_age_days* (and cascading messages)."""
    if max_age_days <= 0 or store is None:
        return 0
    fn = getattr(store, "purge_sessions_older_than_days", None)
    if callable(fn):
        try:
            return int(fn(max_age_days) or 0)
        except Exception as exc:
            logger.warning("session retention failed: %s", exc)
            return 0
    # Fallback: list + delete if store exposes those APIs
    list_fn = getattr(store, "list_sessions", None) or getattr(store, "list_chat_sessions", None)
    del_fn = getattr(store, "delete_session", None) or getattr(store, "delete_chat_session", None)
    if not callable(list_fn) or not callable(del_fn):
        return 0
    removed = 0
    cutoff = time.time() - (max_age_days * 86400)
    try:
        sessions = list_fn()
        if hasattr(sessions, "__await__"):
            return 0  # async path needs caller to await dedicated method
        for s in sessions or []:
            ts = getattr(s, "updated_at", None) or getattr(s, "created_at", None)
            if ts is None and isinstance(s, dict):
                ts = s.get("updated_at") or s.get("created_at")
            epoch = _to_epoch(ts)
            if epoch and epoch < cutoff:
                sid = getattr(s, "id", None) if not isinstance(s, dict) else s.get("id")
                if sid:
                    del_fn(str(sid))
                    removed += 1
    except Exception as exc:
        logger.warning("session retention fallback failed: %s", exc)
    return removed


def _to_epoch(ts: Any) -> float | None:
    if ts is None:
        return None
    if isinstance(ts, (int, float)):
        return float(ts)
    s = str(ts).strip()
    if not s:
        return None
    try:
        from datetime import datetime

        # ISO with optional Z
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        return datetime.fromisoformat(s).timestamp()
    except Exception:
        return None


def run_retention_pass(
    cfg: dict[str, Any] | None = None,
    *,
    store: Any | None = None,
    home: Path | str | None = None,
) -> dict[str, int]:
    """Apply configured retention. Safe to call at serve startup."""
    policy = RetentionPolicy.from_config(cfg)
    home_path = Path(home).expanduser().resolve() if home else _home_from_cfg(cfg)
    result = {
        "attachments": purge_attachments(home_path, max_age_days=policy.attachment_days),
        "computer_shots": purge_computer_shots(
            home_path, max_age_days=policy.computer_shot_days
        ),
        "undo": purge_undo(home_path, max_age_days=policy.undo_days),
        "logs": purge_logs(home_path, max_age_days=policy.log_days),
        "sessions": 0,
    }
    if store is not None and policy.session_days > 0:
        result["sessions"] = purge_old_sessions(store, max_age_days=policy.session_days)
    total = sum(result.values())
    if total:
        logger.info("retention pass removed %s items: %s", total, result)
    return result


def memory_encryption_requested(cfg: dict[str, Any] | None = None) -> bool:
    """True when owner opted into encrypting memory.db at rest."""
    raw = cfg if isinstance(cfg, dict) else {}
    nested = raw.get("retention") if isinstance(raw.get("retention"), dict) else {}
    for src in (nested, raw):
        if "memory_encrypt" in src:
            return bool(src.get("memory_encrypt"))
        if "memory_db_encrypt" in src:
            return bool(src.get("memory_db_encrypt"))
    return False


def _memory_key_material(cfg: dict[str, Any] | None = None) -> str:
    """Derive or load a stable key for optional SQLCipher (never log this)."""
    import hashlib
    import secrets

    home = _home_from_cfg(cfg)
    key_path = home / "auth" / "memory_db.key"
    try:
        if key_path.is_file():
            raw = key_path.read_text(encoding="utf-8").strip()
            if raw.startswith("{"):
                import base64
                import json

                outer = json.loads(raw)
                if isinstance(outer, dict) and outer.get("v") == 2 and outer.get("dpapi"):
                    from remedy.interfaces.secret_store import _dpapi_unprotect

                    plain = _dpapi_unprotect(base64.b64decode(str(outer["dpapi"])))
                    decoded = plain.decode("utf-8").strip()
                    if len(decoded) >= 16:
                        return decoded
            elif len(raw) >= 16:
                return raw
    except Exception:
        pass
    # Prefer DPAPI seal when available
    tok = ""
    try:
        from remedy.interfaces.local_auth import load_local_api_token

        tok = load_local_api_token(home) or ""
    except Exception:
        tok = ""
    if tok:
        key = hashlib.sha256(f"remedy-memory-v1:{tok}".encode()).hexdigest()
    else:
        key = secrets.token_hex(32)
    try:
        key_path.parent.mkdir(parents=True, exist_ok=True)
        # Best-effort DPAPI envelope for the key file on Windows
        try:
            from remedy.interfaces.secret_store import _dpapi_available, _dpapi_protect
            import base64
            import json

            if _dpapi_available():
                blob = base64.b64encode(_dpapi_protect(key.encode("utf-8"))).decode("ascii")
                key_path.write_text(
                    json.dumps({"v": 2, "dpapi": blob}), encoding="utf-8"
                )
            else:
                key_path.write_text(key, encoding="utf-8")
        except Exception:
            key_path.write_text(key, encoding="utf-8")
        try:
            import os

            if os.name == "nt":
                from remedy.interfaces.secret_store import harden_auth_file_acl

                harden_auth_file_acl(key_path)
        except Exception:
            pass
    except OSError as exc:
        logger.debug("could not persist memory key: %s", exc)
    return key


def apply_memory_encryption_pragma(conn: Any, cfg: dict[str, Any] | None = None) -> str:
    """Best-effort SQLCipher keying when ``memory_encrypt`` is on.

    Returns mode: ``off`` | ``sqlcipher`` | ``unavailable``.
    Without a SQLCipher-linked sqlite build, we log once and leave plaintext.
    Never invent a weak XOR scheme that looks like encryption.
    """
    if not memory_encryption_requested(cfg):
        return "off"
    key = _memory_key_material(cfg)
    if not key:
        logger.warning(
            "memory_encrypt enabled but no key material — leaving memory.db plaintext"
        )
        return "unavailable"
    try:
        # SQLCipher uses PRAGMA key; plain sqlite3 typically ignores or errors.
        safe = key.replace("'", "''")
        conn.execute(f"PRAGMA key = '{safe}'")
        conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
        # If we got here on stock sqlite3, encryption is NOT active — detect via
        # cipher_version when present.
        try:
            row = conn.execute("PRAGMA cipher_version").fetchone()
            if not row or not row[0]:
                logger.warning(
                    "memory_encrypt on but SQLCipher not linked (no cipher_version) — "
                    "memory.db remains plaintext until a SQLCipher build is installed"
                )
                return "unavailable"
        except Exception:
            logger.warning(
                "memory_encrypt on but SQLCipher not available — memory.db plaintext"
            )
            return "unavailable"
        logger.info("memory.db opened with SQLCipher")
        return "sqlcipher"
    except Exception as exc:
        logger.warning(
            "memory_encrypt requested but SQLCipher unavailable (%s) — "
            "install a SQLCipher-enabled sqlite build or disable memory_encrypt",
            exc,
        )
        return "unavailable"
