"""Immune organ — silent critical-claim guards (verify + governor)."""

from __future__ import annotations

from remedy.core.metabolism.organism import immune_pulse
from remedy.core.metabolism.verify import VerifyResult, should_verify_text, verify_critical

__all__ = [
    "immune_pulse",
    "VerifyResult",
    "should_verify_text",
    "verify_critical",
]
