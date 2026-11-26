"""Peer contacts registry in {home}/interop/peers.json."""

from __future__ import annotations

import time
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.core.organism_knobs import resolve_int
from remedy.interop.paths import peers_path
from remedy.interop.redact import redact

PROTOCOLS = ("a2a", "mcp")
DEFAULT_PERMISSIONS = ("ask", "task_status", "share_context")
ALL_PERMISSIONS = (
    "ask",
    "delegate",
    "task_status",
    "share_context",
    "spend",
    "send",
    "delete",
    "irreversible",
)


def empty_store() -> dict[str, Any]:
    return {"version": 1, "peers": {}}


def load(home: str | None = None) -> dict[str, Any]:
    path = peers_path(home)
    if not path.is_file():
        return empty_store()
    try:
        raw = path.read_text(encoding="utf-8")
        data = __import__("json").loads(raw)
    except (OSError, ValueError):
        return empty_store()
    if not isinstance(data, dict):
        return empty_store()
    peers = data.get("peers")
    if not isinstance(peers, dict):
        data["peers"] = {}
    data.setdefault("version", 1)
    return data


def save(store: dict[str, Any], home: str | None = None) -> None:
    path = peers_path(home)
    clean: dict[str, Any] = {"version": 1, "peers": {}}
    for key, peer in (store.get("peers") or {}).items():
        if isinstance(peer, dict):
            clean["peers"][str(key)] = redact_peer(peer)
    write_json_atomic(path, clean)


def redact_peer(peer: dict[str, Any]) -> dict[str, Any]:
    out = dict(peer)
    # Never persist raw credentials. auth_ref may name an env var.
    for secret_key in ("api_key", "token", "authorization", "secret", "password"):
        out.pop(secret_key, None)
    return out


def get(store: dict[str, Any], peer_id: str) -> dict[str, Any] | None:
    peers = store.get("peers") or {}
    item = peers.get(peer_id)
    return dict(item) if isinstance(item, dict) else None


def upsert(store: dict[str, Any], peer: dict[str, Any], *, home: str | None = None, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    peer_id = str(peer.get("id") or "").strip()
    if not peer_id:
        raise ValueError("peer id is required")
    cap = resolve_int("interop_max_peers", cfg)
    peers: dict[str, Any] = dict(store.get("peers") or {})
    if peer_id not in peers and len(peers) >= cap:
        raise ValueError(f"peer registry full (max {cap})")
    now = time.time()
    existing = peers.get(peer_id)
    if not isinstance(existing, dict):
        existing = {}
    protocol = str(peer.get("protocol") or existing.get("protocol") or "a2a").strip().lower()
    if protocol not in PROTOCOLS:
        raise ValueError(f"unknown protocol: {protocol}")
    perms = peer.get("permissions")
    if perms is None:
        perms = existing.get("permissions") or list(DEFAULT_PERMISSIONS)
    cleaned_perms = [str(p) for p in perms if str(p) in ALL_PERMISSIONS]
    skills = peer.get("skills")
    if skills is None:
        skills = existing.get("skills") or []
    record = {
        **existing,
        "id": peer_id,
        "name": str(peer.get("name") or existing.get("name") or peer_id),
        "protocol": protocol,
        "endpoint": str(peer.get("endpoint") or existing.get("endpoint") or "").strip(),
        "skills": [str(s) for s in skills],
        "permissions": cleaned_perms,
        "did": peer.get("did") if peer.get("did") is not None else existing.get("did"),
        "enabled": bool(peer.get("enabled", existing.get("enabled", True))),
        "auth_ref": str(peer.get("auth_ref") or existing.get("auth_ref") or ""),
        "updated_ts": now,
    }
    if "created_ts" not in record:
        record["created_ts"] = existing.get("created_ts") or now
    # Drop any accidental secrets.
    for secret_key in ("api_key", "token", "authorization", "secret", "password"):
        record.pop(secret_key, None)
    peers[peer_id] = record
    store = {"version": 1, "peers": peers}
    save(store, home)
    return record


def remove(store: dict[str, Any], peer_id: str, *, home: str | None = None) -> bool:
    peers = dict(store.get("peers") or {})
    if peer_id not in peers:
        return False
    peers.pop(peer_id)
    save({"version": 1, "peers": peers}, home)
    return True


def list_peers(store: dict[str, Any], *, enabled_only: bool = False) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in (store.get("peers") or {}).values():
        if not isinstance(item, dict):
            continue
        if enabled_only and not item.get("enabled", True):
            continue
        out.append(dict(item))
    out.sort(key=lambda p: str(p.get("name") or p.get("id") or ""))
    return out
