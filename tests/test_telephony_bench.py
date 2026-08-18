"""Phase 0 gate: the scripted calls must clear the human bar.

These run in real time (a scripted call is a scripted call), so they are the
slowest tests in the suite by design — the thing being measured is latency, and
you cannot measure latency without spending it.
"""

from __future__ import annotations

import pytest

from remedy.telephony.backends.fake import Cue, FakeBackend, Utterance
from remedy.telephony.bench import (
    SCENARIOS,
    EngineLatency,
    clinic_booking,
    run_scenario,
)
from remedy.telephony.line import CallDirection, CallState
from remedy.telephony.timing import timing_is_trustworthy


async def _skip_if_the_machine_cannot_keep_time():
    """Guard for the timing tests.

    A busy host cannot hold a 20 ms frame, which drifts both sides of the
    simulated call and manufactures overlaps that say nothing about the code.
    Skipping is honest; failing would blame the pipeline for the load.
    """
    ok, overshoot = await timing_is_trustworthy()
    if not ok:
        pytest.skip(
            f"machine cannot hold a 20 ms frame right now "
            f"(median overshoot {overshoot:.1f} ms) — timing not measurable"
        )


@pytest.mark.asyncio
async def test_fake_backend_reports_honest_capabilities():
    caps = FakeBackend().capabilities()
    assert caps.outbound and caps.inbound and caps.full_duplex
    assert caps.sample_rate == 8000
    assert caps.ready


@pytest.mark.asyncio
async def test_fake_call_runs_the_script_and_hangs_up():
    backend = FakeBackend(script=[Utterance("Hello?", cue=Cue.AT_TIME, offset_ms=100)])
    call = await backend.place("+15550100")
    assert call.direction is CallDirection.OUTBOUND
    # place() returns before the driver task has been scheduled, so the call is
    # still IDLE for an instant. Waiting on `live` would fall straight through.
    while call.state is not CallState.ENDED:
        assert await call.wait_state(timeout=5.0) is not None
    assert call.state is CallState.ENDED
    assert [s.text for s in call.spans] == ["Hello?"]


@pytest.mark.asyncio
async def test_the_line_carries_comfort_noise_between_words():
    """Endpointing depends on quiet frames continuing to arrive. A backend that
    stops transmitting between utterances deadlocks every turn."""
    backend = FakeBackend(script=[Utterance("Hi.", cue=Cue.AT_TIME, offset_ms=600)])
    call = await backend.place("+15550100")
    frames = 0
    async for _ in call.audio_in():
        frames += 1
        if frames > 40:
            break
    assert frames > 40
    await call.hangup()


@pytest.mark.parametrize("name", sorted(SCENARIOS))
@pytest.mark.asyncio
async def test_scenario_meets_the_human_bar(name):
    result = await run_scenario(SCENARIOS[name])
    assert result.passed, "\n".join(result.failures)
    assert result.metrics.turns, "no turns were measured"


@pytest.mark.asyncio
async def test_barge_in_is_detected_and_fast():
    await _skip_if_the_machine_cannot_keep_time()
    result = await run_scenario(clinic_booking())
    assert result.metrics.barge_ins, "the scripted interruption never landed"
    worst = max(b.latency_ms for b in result.metrics.barge_ins)
    assert worst <= result.metrics.bar.barge_in_ms


@pytest.mark.asyncio
async def test_slower_engines_break_the_bar():
    """The bar has to be losable, or passing it means nothing. Headroom is
    under 1.5x: at that point the answer lands so late the far end restarts."""
    slow = EngineLatency(
        stt_final_ms=480.0, llm_ttft_ms=960.0, llm_token_ms=12.0,
        tts_ttfb_ms=420.0, tts_chunk_ms=40.0,
    )
    result = await run_scenario(clinic_booking(), latency=slow)
    assert not result.passed
    assert result.failures
