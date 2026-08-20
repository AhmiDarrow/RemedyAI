"""Telephony — her voice on the wire.

Design and phasing: ``docs/TELEPHONY.md``. Phase 0 is the bench: the transport
abstraction, a simulated phone circuit, and the measured human bar, all with no
hardware and no minutes spent.
"""

from remedy.telephony.backends.sip_direct import SipDirectBackend
from remedy.telephony.line import (
    AudioFrame,
    Call,
    CallDirection,
    CallState,
    Capabilities,
    EndReason,
    Line,
    LineBackend,
    silence,
)
from remedy.telephony.options import LineOption, choose, chosen, offer
from remedy.telephony.timing import FramePacer, precise_timing

__all__ = [
    "AudioFrame",
    "Call",
    "CallDirection",
    "CallState",
    "Capabilities",
    "EndReason",
    "FramePacer",
    "Line",
    "LineBackend",
    "LineOption",
    "choose",
    "chosen",
    "SipDirectBackend",
    "offer",
    "precise_timing",
    "silence",
]
