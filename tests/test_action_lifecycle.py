"""v0.33 — RUNNING cannot skip to COMPLETED."""

from __future__ import annotations

import pytest

from remedy.execution.action import (
    ActionRecord,
    ActionState,
    Idempotency,
    IllegalTransition,
    RetryKind,
    classify_idempotency,
    retry_kind,
)
from remedy.verification.evidence import VerificationResult, VerificationStatus


def test_running_cannot_complete_without_verify():
    rec = ActionRecord(tool="bash_exec")
    rec.advance(ActionState.AUTHORIZED)
    rec.advance(ActionState.RUNNING)
    with pytest.raises(IllegalTransition):
        rec.advance(ActionState.COMPLETED)
    assert rec.state == ActionState.RUNNING


def test_happy_path_with_verification():
    rec = ActionRecord(tool="file_read")
    rec.advance(ActionState.AUTHORIZED)
    rec.advance(ActionState.RUNNING)
    rec.advance(ActionState.RESULT)
    rec.advance(ActionState.VERIFYING)
    rec.verification = VerificationResult(
        status=VerificationStatus.PASS, reason="ok"
    )
    rec.advance(ActionState.VERIFIED)
    rec.advance(ActionState.COMPLETED)
    assert rec.state == ActionState.COMPLETED


def test_fail_verification_cannot_complete():
    rec = ActionRecord(tool="file_write")
    rec.advance(ActionState.AUTHORIZED)
    rec.advance(ActionState.RUNNING)
    rec.advance(ActionState.RESULT)
    rec.advance(ActionState.VERIFYING)
    rec.verification = VerificationResult(
        status=VerificationStatus.FAIL, reason="missing file"
    )
    rec.advance(ActionState.VERIFIED)
    with pytest.raises(IllegalTransition):
        rec.advance(ActionState.COMPLETED)
    assert rec.state == ActionState.VERIFIED


def test_mail_is_not_blindly_retried():
    assert classify_idempotency("mail_send") == Idempotency.NON_IDEMPOTENT
    assert retry_kind("mail_send") == RetryKind.NON_RETRYABLE
    rec = ActionRecord(tool="mail_send")
    assert rec.idempotency == Idempotency.NON_IDEMPOTENT
    assert rec.retry == RetryKind.NON_RETRYABLE
    rec.advance(ActionState.AUTHORIZED)
    rec.advance(ActionState.RUNNING)
    rec.advance(ActionState.FAILED)
    with pytest.raises(IllegalTransition):
        rec.advance(ActionState.RETRYING)
    assert rec.state == ActionState.FAILED
