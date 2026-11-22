"""Pluggable peer identity. ClaimIDX is optional and never required."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from remedy.core.organism_knobs import resolve_bool

DID_RE = re.compile(r"^did:[a-z0-9]+:[A-Za-z0-9._:%-]+$", re.IGNORECASE)
CLAIMIDX_DID_RE = re.compile(r"^did:claimidx:[A-Za-z0-9._-]+$", re.IGNORECASE)


@dataclass(frozen=True)
class TrustResult:
    level: str  # untrusted | unverified | verified
    did: str | None
    reason: str
    verifier: str


class IdentityVerifier(Protocol):
    name: str

    def verify(self, peer: dict[str, Any]) -> TrustResult: ...


class NullVerifier:
    """Default: a well-formed DID is unverified, never automatically trusted."""

    name = "none"

    def verify(self, peer: dict[str, Any]) -> TrustResult:
        did = _did_of(peer)
        if not did:
            return TrustResult("unverified", None, "no did presented", self.name)
        if not DID_RE.match(did):
            return TrustResult("untrusted", did, "malformed did", self.name)
        return TrustResult("unverified", did, "no verifier configured", self.name)


class ClaimidxVerifier:
    """Optional ClaimIDX check. Failure or a down host stays unverified."""

    name = "claimidx"

    def verify(self, peer: dict[str, Any]) -> TrustResult:
        did = _did_of(peer)
        if not did:
            return TrustResult("unverified", None, "no did presented", self.name)
        if not DID_RE.match(did):
            return TrustResult("untrusted", did, "malformed did", self.name)
        if did.lower().startswith("did:claimidx:") and not CLAIMIDX_DID_RE.match(did):
            return TrustResult("untrusted", did, "malformed did:claimidx", self.name)
        # Optional live probe: if the local ClaimIDX host is up, treat a
        # well-formed did:claimidx (or any did:* the host acknowledges) as
        # verified-format. Cryptographic proof is left to a tighter plugin.
        if _claimidx_host_healthy():
            if did.lower().startswith("did:claimidx:") or did.lower().startswith("did:key:"):
                return TrustResult("verified", did, "claimidx host reachable; did well-formed", self.name)
        return TrustResult("unverified", did, "claimidx not confirmed", self.name)


def verifier_for(cfg: dict[str, Any] | None = None, override: IdentityVerifier | None = None) -> IdentityVerifier:
    if override is not None:
        return override
    if resolve_bool("interop_claimidx", cfg):
        return ClaimidxVerifier()
    return NullVerifier()


def _did_of(peer: dict[str, Any]) -> str | None:
    for key in ("did", "identity", "id"):
        value = peer.get(key)
        if isinstance(value, str) and value.lower().startswith("did:"):
            return value.strip()
    return None


def _claimidx_host_healthy() -> bool:
    try:
        from remedy.runtime.claimidx_host import is_healthy

        return bool(is_healthy())
    except Exception:
        return False
