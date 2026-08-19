"""Portable Soul Field — export/import personhood between machines.

Uses the same authenticated encrypt package format as partner identity, or a
plain redacted JSON for opt-in unencrypted local transfer.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.memory.soul.field import (
    SoulField,
    clear_soul_cache,
    load_soul_field,
    looks_like_secret_soul,
    save_soul_field,
    soul_dir,
)

SOUL_EXPORT_VERSION = 1


def soul_export_payload(home: str | Path | None = None) -> dict[str, Any]:
    """Redacted soul dict for packaging (no secrets)."""
    sf = load_soul_field(home)
    raw = sf.to_dict()
    # Scrub secret-shaped strings from free text fields
    def scrub_list(items: list[Any]) -> list[Any]:
        out: list[Any] = []
        for it in items or []:
            if isinstance(it, str):
                if looks_like_secret_soul(it):
                    continue
                out.append(it[:400])
            elif isinstance(it, dict):
                text = json.dumps(it, default=str)
                if looks_like_secret_soul(text):
                    continue
                out.append(it)
            else:
                out.append(it)
        return out

    return {
        "format": "remedy-soul-field",
        "version": SOUL_EXPORT_VERSION,
        "exported_at": time.time(),
        "soul": {
            "schema": raw.get("schema"),
            "identity_name": raw.get("identity_name"),
            "identity_gender": raw.get("identity_gender") or "female",
            "identity_vow": raw.get("identity_vow"),
            "self_habits": scrub_list(raw.get("self_habits") or []),
            "relational": raw.get("relational") or {},
            "episodes": scrub_list(raw.get("episodes") or [])[-12:],
            "organism_lessons": scrub_list(raw.get("organism_lessons") or [])[-16:],
            "pledges": scrub_list(raw.get("pledges") or []),
            "future_dreams": scrub_list(raw.get("future_dreams") or []),
            "updated_ts": raw.get("updated_ts"),
        },
    }


def export_soul_plain(
    dest: str | Path,
    home: str | Path | None = None,
) -> Path:
    """Write redacted JSON (no passphrase). Path under home/exports when relative."""
    payload = soul_export_payload(home)
    path = Path(dest).expanduser()
    if not path.is_absolute():
        path = soul_dir(home).parent / "exports" / path.name
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json_atomic(path, payload, ensure_ascii=False)
    return path


def export_soul_encrypted(
    dest: str | Path,
    *,
    passphrase: str,
    home: str | Path | None = None,
) -> Path:
    """Encrypted package via identity_export crypto primitives."""
    from remedy.core.metabolism.identity_export import export_identity

    payload = soul_export_payload(home)
    # Reuse identity envelope (format still remedy-partner-identity at wire level;
    # payload carries format=remedy-soul-field for merge recognition).
    return export_identity(payload, dest, passphrase=passphrase, home=home)


def import_soul_payload(
    payload: dict[str, Any],
    home: str | Path | None = None,
    *,
    merge: bool = True,
) -> dict[str, Any]:
    """Merge or replace soul field from payload. Returns counts."""
    soul_raw = payload.get("soul") if isinstance(payload, dict) else None
    if not isinstance(soul_raw, dict):
        # Full field dump
        if isinstance(payload, dict) and "relational" in payload:
            soul_raw = payload
        else:
            raise ValueError("not a soul field payload")

    incoming = SoulField.from_dict(soul_raw)
    if not merge:
        clear_soul_cache()
        save_soul_field(incoming, home)
        return {"ok": True, "mode": "replace", "episodes": len(incoming.episodes)}

    current = load_soul_field(home)
    # Merge: keep higher bond, union pledges/habits/lessons, append episodes
    current.relational.rapport = max(
        current.relational.rapport, incoming.relational.rapport
    )
    current.relational.trust = max(current.relational.trust, incoming.relational.trust)
    current.relational.turns_together = max(
        current.relational.turns_together, incoming.relational.turns_together
    )
    if incoming.relational.help_mode and not current.relational.help_mode:
        current.relational.help_mode = incoming.relational.help_mode
    if incoming.relational.correction_style and not current.relational.correction_style:
        current.relational.correction_style = incoming.relational.correction_style
    for v in incoming.relational.voice_markers:
        if v not in current.relational.voice_markers:
            current.relational.voice_markers.append(v)
    for t in incoming.relational.open_threads:
        if t not in current.relational.open_threads:
            current.relational.open_threads.append(t)
    for t in incoming.relational.tensions:
        if t not in current.relational.tensions:
            current.relational.tensions.append(t)
    for p in incoming.pledges:
        if p not in current.pledges:
            current.pledges.append(p)
    for h in incoming.self_habits:
        if h not in current.self_habits:
            current.self_habits.append(h)
    for d in getattr(incoming, "future_dreams", None) or []:
        if d not in current.future_dreams:
            current.future_dreams.append(d)
    for les in incoming.organism_lessons:
        current.organism_lessons.append(les)
    for ep in incoming.episodes:
        current.episodes.append(ep)
    if incoming.identity_vow and len(incoming.identity_vow) > 20:
        # Keep local vow unless empty
        if not (current.identity_vow or "").strip():
            current.identity_vow = incoming.identity_vow
    current.touch()
    save_soul_field(current, home)
    return {
        "ok": True,
        "mode": "merge",
        "episodes": len(current.episodes),
        "pledges": len(current.pledges),
        "habits": len(current.self_habits),
    }


def import_soul_file(
    source: str | Path,
    *,
    passphrase: str = "",
    home: str | Path | None = None,
    merge: bool = True,
) -> dict[str, Any]:
    """Load plain or encrypted soul package."""
    path = Path(source).expanduser()
    if not path.is_file():
        raise ValueError("soul package not found")
    raw_text = path.read_text(encoding="utf-8")
    package = json.loads(raw_text)
    if not isinstance(package, dict):
        raise ValueError("invalid package")
    if package.get("format") == "remedy-soul-field" and "soul" in package:
        return import_soul_payload(package, home, merge=merge)
    if package.get("format") == "remedy-partner-identity":
        from remedy.core.metabolism.identity_export import import_identity

        if not (passphrase or "").strip():
            raise ValueError("passphrase required for encrypted package")
        payload = import_identity(str(path), passphrase=passphrase)
        # May be full identity with nested soul, or pure soul payload
        if "soul" in payload:
            return import_soul_payload(payload, home, merge=merge)
        # Identity package: still try soul key from collect
        if payload.get("format") == "remedy-soul-field":
            return import_soul_payload(payload, home, merge=merge)
        raise ValueError("identity package has no soul field — re-export with soul")
    if "relational" in package or "identity_vow" in package:
        return import_soul_payload({"soul": package}, home, merge=merge)
    raise ValueError("unrecognized soul package format")
