"""The capability probe — the thing that replaces a setup wizard.

Its output is read aloud mid-conversation, so these tests care as much about the
sentences as about the booleans.
"""

from __future__ import annotations

from remedy.telephony.line import Capabilities
from remedy.telephony.registry import (
    BackendProbe,
    TelephonyStatus,
    probe_all,
    probe_bench,
)

READY = Capabilities(outbound=True, inbound=True)


def _status(**states: bool) -> TelephonyStatus:
    probes = [probe_bench()]
    for name, ok in states.items():
        probes.append(
            BackendProbe(
                name=name,
                capabilities=READY if ok else Capabilities(),
                missing=() if ok else (f"{name} is not set up",),
                action="" if ok else f"do the {name} thing",
            )
        )
    return TelephonyStatus(probes=probes)


def test_probe_never_raises_and_is_fast_enough_to_run_mid_sentence():
    status = probe_all()
    assert status.probes
    assert status.get("bench") is not None


def test_bench_is_always_available_but_is_not_a_real_call():
    status = probe_all()
    assert probe_bench().ready
    # A simulated call must never make her claim she can phone people.
    assert not any(p.name == "bench" for p in status.ready)


def test_phone_bridge_needs_both_control_and_audio():
    """ADB dials; Bluetooth carries the voice. Either alone is not a call."""
    assert not _status(android=True, bluetooth_hfp=False, sip=False).can_call
    assert not _status(android=False, bluetooth_hfp=True, sip=False).can_call
    assert _status(android=True, bluetooth_hfp=True, sip=False).can_call


def test_sip_alone_is_enough():
    assert _status(android=False, bluetooth_hfp=False, sip=True).can_call


def test_says_it_plainly_when_ready():
    assert _status(android=True, bluetooth_hfp=True, sip=False).say() == (
        "I can make and take calls."
    )


def test_counts_the_missing_things_correctly():
    """"Two things" followed by three things is the kind of small wrongness that
    makes everything else she says sound unreliable."""
    said = _status(android=False, bluetooth_hfp=False, sip=False).say()
    assert said.startswith("Not yet, two things.")
    assert "1)" in said and "2)" in said and "3)" not in said


def test_does_not_mention_sip_when_the_phone_bridge_is_the_blocker():
    """Nobody wants to hear about a SIP engine when the real blocker is a
    $10 dongle. Only the nearest working path gets named."""
    said = _status(android=False, bluetooth_hfp=False, sip=False).say()
    assert "sip" not in said.lower()


def test_single_blocker_reads_as_almost_there():
    said = _status(android=True, bluetooth_hfp=False, sip=False).say()
    assert said.startswith("Almost")
    assert "bluetooth_hfp is not set up" in said


def test_every_gap_carries_an_action_the_owner_can_take():
    """A gap with no next step is a dead end in a conversation."""
    for probe in probe_all().probes:
        if probe.missing and probe.name != "bench":
            assert probe.action or probe.detail, f"{probe.name} states a gap with no way out"


def test_spoken_status_has_no_error_codes():
    said = probe_all().say()
    for shouty in ("ERR", "None", "Traceback", "Exception", "null"):
        assert shouty not in said
