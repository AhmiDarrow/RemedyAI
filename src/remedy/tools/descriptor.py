"""Canonical description of a tool. Security knowledge lives here, not in name lists."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum

from remedy.policy.capabilities import Capability
from remedy.policy.risk import Risk


class CredentialPolicy(StrEnum):
    NONE = "none"
    EXPLICIT = "explicit"
    AMBIENT = "ambient"  # today's inherit-env; removed in M1.4


class NetworkPolicy(StrEnum):
    DENIED = "denied"
    ALLOWED = "allowed"


@dataclass(frozen=True, slots=True)
class ToolDescriptor:
    name: str
    capabilities: frozenset[Capability]
    risk: Risk
    requires_approval: bool
    requires_verification: bool = False
    credential_policy: CredentialPolicy = CredentialPolicy.NONE
    network_policy: NetworkPolicy = NetworkPolicy.DENIED
    timeout: timedelta | None = None
