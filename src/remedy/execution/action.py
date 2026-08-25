"""v0.33 action state machine. RUNNING cannot skip to COMPLETED."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import uuid4

from remedy.verification.evidence import VerificationResult, VerificationStatus


class ActionState(StrEnum):
    PROPOSED = "PROPOSED"
    AUTHORIZED = "AUTHORIZED"
    RUNNING = "RUNNING"
    RESULT = "RESULT"
    VERIFYING = "VERIFYING"
    VERIFIED = "VERIFIED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    RETRYING = "RETRYING"
    REPLANNING = "REPLANNING"
    ESCALATED = "ESCALATED"


class Idempotency(StrEnum):
    IDEMPOTENT = "IDEMPOTENT"
    CONDITIONALLY_IDEMPOTENT = "CONDITIONALLY_IDEMPOTENT"
    NON_IDEMPOTENT = "NON_IDEMPOTENT"


class RetryKind(StrEnum):
    RETRYABLE = "retryable"
    NON_RETRYABLE = "non_retryable"
    REPLAN_REQUIRED = "replan_required"
    APPROVAL_REQUIRED = "approval_required"
    ROLLBACK_REQUIRED = "rollback_required"


class FailureClass(StrEnum):
    AUTH = "AUTH"
    NETWORK = "NETWORK"
    TOOL = "TOOL"
    ENVIRONMENT = "ENVIRONMENT"
    INPUT = "INPUT"
    MODEL = "MODEL"
    POLICY = "POLICY"
    VERIFICATION = "VERIFICATION"
    USER = "USER"


_ALLOWED: dict[ActionState, frozenset[ActionState]] = {
    ActionState.PROPOSED: frozenset(
        {ActionState.AUTHORIZED, ActionState.FAILED, ActionState.ESCALATED}
    ),
    ActionState.AUTHORIZED: frozenset(
        {ActionState.RUNNING, ActionState.FAILED, ActionState.ESCALATED}
    ),
    ActionState.RUNNING: frozenset({ActionState.RESULT, ActionState.FAILED}),
    ActionState.RESULT: frozenset({ActionState.VERIFYING, ActionState.FAILED}),
    ActionState.VERIFYING: frozenset(
        {
            ActionState.VERIFIED,
            ActionState.FAILED,
            ActionState.RETRYING,
            ActionState.REPLANNING,
        }
    ),
    ActionState.VERIFIED: frozenset({ActionState.COMPLETED}),
    ActionState.RETRYING: frozenset(
        {ActionState.AUTHORIZED, ActionState.FAILED, ActionState.ESCALATED}
    ),
    ActionState.REPLANNING: frozenset(
        {ActionState.PROPOSED, ActionState.FAILED, ActionState.ESCALATED}
    ),
    ActionState.FAILED: frozenset(
        {ActionState.RETRYING, ActionState.REPLANNING, ActionState.ESCALATED}
    ),
    ActionState.COMPLETED: frozenset(),
    ActionState.ESCALATED: frozenset(),
}

_COMPLETABLE_VERIFICATION = frozenset(
    {
        VerificationStatus.PASS,
        VerificationStatus.NOT_REQUIRED,
        VerificationStatus.INCONCLUSIVE,
    }
)


class IllegalTransition(ValueError):  # noqa: N818 — domain name used in tests
    pass


@dataclass
class ActionRecord:
    tool: str
    state: ActionState = ActionState.PROPOSED
    action_id: str = field(default_factory=lambda: str(uuid4()))
    idempotency: Idempotency = Idempotency.CONDITIONALLY_IDEMPOTENT
    retry: RetryKind = RetryKind.RETRYABLE
    verification: VerificationResult | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        self.idempotency = classify_idempotency(self.tool)
        self.retry = retry_kind(self.tool)

    def advance(self, dest: ActionState) -> None:
        if dest == ActionState.COMPLETED and self.state == ActionState.RUNNING:
            raise IllegalTransition("RUNNING cannot skip verification to COMPLETED")
        allowed = _ALLOWED.get(self.state, frozenset())
        if dest not in allowed:
            raise IllegalTransition(f"{self.state.value} → {dest.value} is not allowed")
        if dest == ActionState.RETRYING and self.retry == RetryKind.NON_RETRYABLE:
            raise IllegalTransition(f"{self.tool} is not retryable")
        if dest == ActionState.COMPLETED:
            vr = self.verification
            # INCONCLUSIVE still may complete a shell step; FAIL cannot.
            if vr is None or vr.status not in _COMPLETABLE_VERIFICATION:
                raise IllegalTransition("cannot complete without a non-failing verification")
        self.state = dest


def classify_idempotency(tool: str) -> Idempotency:
    n = (tool or "").strip()
    if n in ("mail_send", "mail_reply") or n.startswith("computer_"):
        return Idempotency.NON_IDEMPOTENT
    if "delete" in n or n in ("calendar_cancel_event",):
        return Idempotency.NON_IDEMPOTENT
    if n in ("file_read", "list_dir", "web_search"):
        return Idempotency.IDEMPOTENT
    return Idempotency.CONDITIONALLY_IDEMPOTENT


def retry_kind(tool: str) -> RetryKind:
    if classify_idempotency(tool) == Idempotency.NON_IDEMPOTENT:
        return RetryKind.NON_RETRYABLE
    return RetryKind.RETRYABLE
