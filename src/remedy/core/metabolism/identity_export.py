"""Portable partner identity — encrypted export/import (user-owned, no cloud).

Default package excludes API keys, OAuth tokens, raw evidence blobs, and IR.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from typing import Any

EXPORT_VERSION = 1


def _derive_key(passphrase: str, salt: bytes) -> bytes:
    """PBKDF2-HMAC-SHA256 — stdlib only (enc + mac material)."""
    return hashlib.pbkdf2_hmac(
        "sha256",
        (passphrase or "").encode("utf-8"),
        salt,
        200_000,
        dklen=64,  # 32 enc + 32 mac
    )


def _xor_stream(data: bytes, key: bytes) -> bytes:
    """SHA256 keystream XOR (stdlib). Authenticated via HMAC on package."""
    out = bytearray()
    block = b""
    counter = 0
    for i, b in enumerate(data):
        if i % 32 == 0:
            block = hashlib.sha256(key + counter.to_bytes(8, "big")).digest()
            counter += 1
        out.append(b ^ block[i % 32])
    return bytes(out)


def _safe_export_path(dest: Path | str, *, home: Path | str | None = None) -> Path:
    """Resolve dest; refuse path escape outside home/exports when home set."""
    path = Path(dest).expanduser()
    # Disallow null bytes / wild junk
    if "\x00" in str(path):
        raise ValueError("invalid export path")
    if home is not None:
        root = (Path(home).expanduser().resolve() / "exports").resolve()
        root.mkdir(parents=True, exist_ok=True)
        # If dest is bare filename, force under exports
        if not path.is_absolute() and path.parent == Path("."):
            path = root / path.name
        try:
            resolved = path.resolve()
            resolved.relative_to(root)
        except Exception as exc:
            raise ValueError(
                f"export path must stay under {root}"
            ) from exc
        return resolved
    return path.expanduser()


def build_identity_payload(
    *,
    partner_memory: list[dict[str, Any]] | None = None,
    skill_ranks: list[dict[str, Any]] | None = None,
    project_profiles: list[dict[str, Any]] | None = None,
    time_crystal: list[dict[str, Any]] | None = None,
    display_name: str = "",
    soul: dict[str, Any] | None = None,
    life_goals: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Assemble redacted portable payload (no secrets fields)."""

    from remedy.core.metabolism.redact import looks_like_secret_text

    _DROP_KEYS = frozenset(
        {
            "api_key",
            "token",
            "password",
            "secret",
            "refresh_token",
            "access_token",
            "client_secret",
            "session_token",
            "id_token",
            "auth_token",
            "private_key",
            "bot_token",
            "app_password",
        }
    )

    def scrub_list(items: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for it in items or []:
            if not isinstance(it, dict):
                continue
            clean = {
                k: v
                for k, v in it.items()
                if str(k).lower() not in _DROP_KEYS
            }
            # Drop values that look like secrets
            text = json.dumps(clean, default=str)
            if looks_like_secret_text(text):
                continue
            out.append(clean)
        return out

    out: dict[str, Any] = {
        "version": EXPORT_VERSION,
        "exported_at": time.time(),
        "display_name": (display_name or "")[:80],
        "partner_memory": scrub_list(partner_memory),
        "skill_ranks": scrub_list(skill_ranks),
        "project_profiles": scrub_list(project_profiles),
        "time_crystal": scrub_list(time_crystal),
        "life_goals": scrub_list(life_goals),
        "excludes": [
            "api_keys",
            "oauth_tokens",
            "evidence_raw",
            "action_ir",
            "provider_credentials",
        ],
    }
    # Soul Field (personhood) — opt-in portable identity tissue
    if isinstance(soul, dict) and soul:
        # Drop secret-shaped free text inside soul dump
        try:
            text = json.dumps(soul, default=str)
            if not looks_like_secret_text(text):
                out["soul"] = soul
                out["format_hint"] = "includes_soul_field"
        except Exception:
            pass
    return out


def export_identity(
    payload: dict[str, Any],
    dest: Path | str,
    *,
    passphrase: str,
    home: Path | str | None = None,
) -> Path:
    """Write authenticated encrypted package to dest. Raises on empty passphrase."""
    import hmac as hmac_mod

    if not (passphrase or "").strip():
        raise ValueError("passphrase required for identity export")
    if len((passphrase or "").strip()) < 8:
        raise ValueError("passphrase must be at least 8 characters")
    salt = secrets.token_bytes(16)
    material = _derive_key(passphrase, salt)
    enc_key, mac_key = material[:32], material[32:]
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    cipher = _xor_stream(raw, enc_key)
    sha = hashlib.sha256(raw).hexdigest()
    mac = hmac_mod.new(
        mac_key,
        salt + cipher + sha.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    package = {
        "format": "remedy-partner-identity",
        "version": EXPORT_VERSION,
        "kdf": "pbkdf2-sha256-200k",
        "cipher": "sha256-xor-stream",
        "mac": "hmac-sha256",
        "salt_b64": base64.b64encode(salt).decode("ascii"),
        "ciphertext_b64": base64.b64encode(cipher).decode("ascii"),
        "sha256": sha,
        "hmac_hex": mac,
    }
    if home is not None:
        path = _safe_export_path(dest, home=home)
    else:
        path = Path(dest).expanduser()
        path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(package, indent=2), encoding="utf-8")
    with contextlib.suppress(Exception):
        os.chmod(path, 0o600)
    return path


def import_identity(
    source: Path | str,
    *,
    passphrase: str,
) -> dict[str, Any]:
    """Decrypt and return payload. Validates HMAC + sha256."""
    import hmac as hmac_mod

    if not (passphrase or "").strip():
        raise ValueError("passphrase required for identity import")
    path = Path(source).expanduser()
    if not path.is_file():
        raise ValueError("identity package not found")
    # Size cap — refuse multi-MB dump
    if path.stat().st_size > 8_000_000:
        raise ValueError("identity package too large")
    package = json.loads(path.read_text(encoding="utf-8"))
    if package.get("format") != "remedy-partner-identity":
        raise ValueError("not a remedy partner identity package")
    salt = base64.b64decode(package["salt_b64"])
    cipher = base64.b64decode(package["ciphertext_b64"])
    material = _derive_key(passphrase, salt)
    enc_key, mac_key = material[:32], material[32:]
    sha = str(package.get("sha256") or "")
    # Authenticate before decrypt (HMAC over salt||cipher||sha). Fail closed:
    # missing/empty hmac_hex is refused (legacy packages must re-export).
    provided = str(package.get("hmac_hex") or "").strip()
    if not provided:
        raise ValueError("identity package missing HMAC (fail closed)")
    expected = hmac_mod.new(
        mac_key,
        salt + cipher + sha.encode("ascii"),
        hashlib.sha256,
    ).hexdigest()
    if not hmac_mod.compare_digest(expected, provided):
        raise ValueError("passphrase incorrect or package corrupted")
    raw = _xor_stream(cipher, enc_key)
    if hashlib.sha256(raw).hexdigest() != sha:
        raise ValueError("passphrase incorrect or package corrupted")
    payload = json.loads(raw.decode("utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("invalid payload")
    return payload


def collect_default_payload(home: Path | str | None = None) -> dict[str, Any]:
    """Best-effort collect from local stores (no credentials)."""
    partner_memory: list[dict[str, Any]] = []
    skill_ranks: list[dict[str, Any]] = []
    project_profiles: list[dict[str, Any]] = []
    time_crystal: list[dict[str, Any]] = []
    display_name = ""

    try:
        from remedy.core.project_learning import load_all

        data = load_all()
        projects = data.get("projects") or {}
        if isinstance(projects, dict):
            for pid, prof in list(projects.items())[:40]:
                if not isinstance(prof, dict):
                    continue
                project_profiles.append(
                    {
                        "id": str(pid)[:32],
                        "path": str(prof.get("path") or "")[:400],
                        "sessions": int(prof.get("sessions") or 0),
                        "turns": int(prof.get("turns") or 0),
                        # No secrets — only learning stats
                        "avg_quality": prof.get("avg_quality"),
                    }
                )
    except Exception:
        pass

    try:
        from remedy.core.metabolism.time_crystal import get_time_crystal

        tc = get_time_crystal("_export")
        time_crystal = tc.export_durable()
    except Exception:
        pass

    try:
        from remedy.core.metabolism.skill_genome import get_skill_genome

        skill_ranks = get_skill_genome().rank(40)
    except Exception:
        pass

    # Profile facts if available (module shape varies by version)
    try:
        from remedy.memory import partner_memory as pm

        who = getattr(pm, "format_whoami", None)
        if callable(who):
            text = str(who(home) if home is not None else who())
            if text and "secret" not in text.lower():
                for line in text.splitlines():
                    line = line.strip(" -*\t")
                    if line and len(line) > 3:
                        partner_memory.append({"text": line[:400]})
                        if not display_name and line.lower().startswith("name"):
                            display_name = line.split(":", 1)[-1].strip()[:80]
    except Exception:
        pass

    soul: dict[str, Any] | None = None
    try:
        from remedy.memory.soul.portable import soul_export_payload

        pack = soul_export_payload(home)
        soul = pack.get("soul") if isinstance(pack, dict) else None
        if not display_name and isinstance(soul, dict):
            display_name = str(soul.get("identity_name") or "")[:80]
    except Exception:
        soul = None

    life_goals: list[dict[str, Any]] = []
    with contextlib.suppress(Exception):
        from remedy.memory.life_goals import LifeGoalStore

        life_goals = [g.to_public() for g in LifeGoalStore(home).list(include_closed=True)]

    return build_identity_payload(
        partner_memory=partner_memory[:80],
        skill_ranks=skill_ranks,
        project_profiles=project_profiles,
        time_crystal=time_crystal,
        display_name=display_name,
        soul=soul if isinstance(soul, dict) else None,
        life_goals=life_goals[:40],
    )
