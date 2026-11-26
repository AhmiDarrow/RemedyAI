"""Consent, trust policy, rate limits, hop limits, and ping-pong guards."""

from __future__ import annotations

import hashlib
import time
from contextlib import suppress
from dataclasses import dataclass
from typing import Any

from remedy.core.atomic_json import write_json_atomic
from remedy.core.organism_knobs import resolve as resolve_knob
from remedy.core.organism_knobs import resolve_int
from remedy.interop.identity import TrustResult
from remedy.interop.paths import rate_path

# These never run for a peer without the owner's approval, even if the peer
# is ClaimIDX-verified and listed with the permission.
ALWAYS_APPROVAL = frozenset({"spend", "transact", "send", "communicate", "delete", "irreversible"})
# Conservative inbound actions an unverified-but-known peer may attempt.
GATED_ALLOWED = frozenset({"ask", "task_status", "share_context"})

TERMINAL_DECISIONS = frozenset({"allow", "deny", "need_approval"})


@dataclass(frozen=True)
class Decision:
    action: str
    outcome: str  # allow | deny | need_approval
    reason: str
    trust: str


def normalize_action(action: str) -> str:
    raw = (action or "ask").strip().lower()
    aliases = {
        "transact": "spend",
        "pay": "spend",
        "money": "spend",
        "communicate": "send",
        "message": "send",
        "remove": "delete",
        "rm": "delete",
    }
    return aliases.get(raw, raw)


def decide(
    action: str,
    *,
    peer: dict[str, Any] | None,
    trust: TrustResult,
    policy: str,
) -> Decision:
    act = normalize_action(action)
    policy = (policy or "gated").strip().lower()
    if policy not in {"open", "gated", "verified"}:
        policy = "gated"

    if act in ALWAYS_APPROVAL:
        return Decision(act, "need_approval", "money, send, and delete always need the owner", trust.level)

    if peer is not None and not peer.get("enabled", True):
        return Decision(act, "deny", "disabled peer", trust.level)

    if peer is None:
        if policy == "verified":
            return Decision(act, "deny", "trust policy requires a verified peer", trust.level)
        if act in ALWAYS_APPROVAL:
            return Decision(act, "need_approval", "money, send, and delete always need the owner", trust.level)
        if policy == "open" and act in GATED_ALLOWED | {"delegate"}:
            return Decision(act, "allow", "open policy, unknown peer", trust.level)
        if act in GATED_ALLOWED:
            return Decision(act, "need_approval", "unknown peer; queued for the owner", trust.level)
        return Decision(act, "deny", "unknown peer", trust.level)

    perms = {str(p) for p in (peer.get("permissions") or [])}
    if act not in perms and act != "task_status":
        # task_status is always allowed for a known peer so they can poll.
        return Decision(act, "deny", f"peer is not permitted to {act}", trust.level)

    if trust.level == "untrusted":
        return Decision(act, "deny", trust.reason or "untrusted peer", trust.level)

    if policy == "verified" and trust.level != "verified":
        return Decision(act, "deny", "trust policy requires a verified peer", trust.level)

    if policy == "gated" and trust.level != "verified" and act not in GATED_ALLOWED:
        return Decision(act, "need_approval", "unverified peers cannot do this without the owner", trust.level)

    return Decision(act, "allow", "permitted", trust.level)


def hop_count(metadata: dict[str, Any] | None) -> int:
    if not metadata:
        return 0
    for key in ("hop", "hopCount", "x-remedy-hop"):
        raw = metadata.get(key)
        if raw is None:
            continue
        try:
            return max(0, int(raw))
        except (TypeError, ValueError):
            continue
    return 0


def check_hop(metadata: dict[str, Any] | None, cfg: dict[str, Any] | None = None) -> str | None:
    limit = resolve_int("interop_hop_limit", cfg)
    hops = hop_count(metadata)
    if hops >= limit:
        return f"hop limit {limit} exceeded"
    return None


def loop_key(peer_id: str, text: str, context_id: str | None = None) -> str:
    basis = f"{peer_id}\n{context_id or ''}\n{(text or '').strip().lower()}"
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:24]


def check_loop(
    home: str | None,
    key: str,
    *,
    window_s: float = 120.0,
    repeats: int = 3,
) -> str | None:
    """Reject the same payload bouncing back and forth in a short window."""
    path = rate_path(home)
    data = _load_json(path)
    loops = data.setdefault("loops", {})
    now = time.time()
    entry = loops.get(key) if isinstance(loops.get(key), dict) else {"times": []}
    times = [float(t) for t in entry.get("times") or [] if now - float(t) <= window_s]
    times.append(now)
    entry["times"] = times[-repeats * 2 :]
    loops[key] = entry
    _prune_and_save(path, data, now)
    if len(times) >= repeats:
        return "ping-pong guard: identical payload repeated"
    return None


def check_rate(home: str | None, peer_id: str, cfg: dict[str, Any] | None = None) -> str | None:
    limit = resolve_int("interop_rate_per_min", cfg)
    path = rate_path(home)
    data = _load_json(path)
    rates = data.setdefault("rates", {})
    now = time.time()
    entry = rates.get(peer_id) if isinstance(rates.get(peer_id), dict) else {"times": []}
    times = [float(t) for t in entry.get("times") or [] if now - float(t) <= 60.0]
    if len(times) >= limit:
        return f"rate limit: {limit} requests per minute"
    times.append(now)
    entry["times"] = times
    rates[peer_id or "_unknown"] = entry
    _prune_and_save(path, data, now)
    return None


def trust_policy(cfg: dict[str, Any] | None = None) -> str:
    value = resolve_knob("interop_trust_policy", cfg)
    return str(value or "gated")


def _load_json(path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        import json

        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _prune_and_save(path, data: dict[str, Any], now: float) -> None:
    for bucket in ("rates", "loops"):
        table = data.get(bucket)
        if not isinstance(table, dict):
            continue
        stale = [k for k, v in table.items() if isinstance(v, dict) and not any(
            now - float(t) <= 300 for t in v.get("times") or [] if _is_float(t)
        )]
        for key in stale:
            table.pop(key, None)
    with suppress(Exception):
        write_json_atomic(path, data)


def _is_float(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
