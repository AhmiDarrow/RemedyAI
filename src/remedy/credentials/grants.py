"""Grant records — scoped, time-bound, auditable, revocable."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import uuid4


@dataclass(frozen=True, slots=True)
class CredentialScope:
    provider: str
    repository: str = ""
    operations: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True, slots=True)
class CredentialGrant:
    grant_id: str
    provider: str
    scope: CredentialScope
    expires_at: datetime
    environment: Mapping[str, str]
    revoked: bool = False

    def alive(self, now: datetime | None = None) -> bool:
        if self.revoked:
            return False
        clock = now or datetime.now(UTC)
        exp = self.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        return clock < exp


@dataclass(frozen=True, slots=True)
class CredentialRequest:
    provider: str
    operations: frozenset[str] = field(default_factory=frozenset)
    repository: str = ""
    ttl: timedelta = timedelta(minutes=15)


def new_grant_id() -> str:
    return str(uuid4())
