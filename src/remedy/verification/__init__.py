"""Verify observed outcomes. Exit code 0 is not success by itself."""

from __future__ import annotations

from remedy.verification.evidence import (
    ActionResult,
    Evidence,
    EvidenceType,
    VerificationResult,
    VerificationStatus,
)
from remedy.verification.verifier import Verifier, verify_action

__all__ = [
    "ActionResult",
    "Evidence",
    "EvidenceType",
    "VerificationResult",
    "VerificationStatus",
    "Verifier",
    "verify_action",
]
