"""Evidence and verification results (v0.32 M1.6)."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import uuid4


class EvidenceType(StrEnum):
    EXIT_CODE = "exit_code"
    FILE_EXISTS = "file_exists"
    GIT_COMMIT = "git_commit"
    HTTP_RESPONSE = "http_response"
    SCREENSHOT = "screenshot"
    TEST_RESULT = "test_result"
    APPLICATION_STATE = "application_state"
    DOM = "dom"
    TEXT = "text"


class VerificationStatus(StrEnum):
    PASS = "pass"
    FAIL = "fail"
    INCONCLUSIVE = "inconclusive"
    NOT_REQUIRED = "not_required"


@dataclass(frozen=True, slots=True)
class Evidence:
    type: EvidenceType
    source: str
    description: str
    data: Mapping[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=lambda: datetime.now(UTC))
    evidence_id: str = field(default_factory=lambda: str(uuid4()))


@dataclass(frozen=True, slots=True)
class ActionResult:
    tool: str
    ok: bool
    exit_code: int | None = None
    stdout: str = ""
    stderr: str = ""
    path: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class VerificationResult:
    status: VerificationStatus
    reason: str
    evidence: tuple[Evidence, ...] = ()
