"""Frame pacing — the difference between a voice and a stutter."""

from __future__ import annotations

import pytest

from remedy.telephony.timing import FramePacer


class _Clock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


async def _late_frame(pacer: FramePacer, clock: _Clock, by_s: float = 0.5) -> None:
    clock.advance(by_s)
    await pacer.wait()


@pytest.mark.asyncio
async def test_the_first_frame_of_a_run_is_never_late():
    clock = _Clock()
    pacer = FramePacer(20.0, clock=clock)
    clock.advance(5.0)
    await pacer.wait()
    assert pacer.late_frames == 0


@pytest.mark.asyncio
async def test_a_frame_past_its_deadline_is_counted():
    clock = _Clock()
    pacer = FramePacer(20.0, clock=clock)
    await pacer.wait()
    await _late_frame(pacer, clock, 0.06)
    assert pacer.late_frames == 1
    assert pacer.worst_late_ms == pytest.approx(40.0, abs=1.0)


@pytest.mark.asyncio
async def test_rebasing_keeps_the_pacing_history():
    """Playout rebases many times a call and the bench reads the counters once
    at the end — zeroing them here reported only the last chunk, and hid a call
    that stuttered throughout."""
    clock = _Clock()
    pacer = FramePacer(20.0, clock=clock)
    await pacer.wait()
    await _late_frame(pacer, clock)
    assert pacer.late_frames == 1

    pacer.rebase()
    assert pacer.late_frames == 1
    assert pacer.worst_late_ms > 0.0

    await pacer.wait()  # free again: a new run
    assert pacer.late_frames == 1

    pacer.clear_stats()
    assert (pacer.late_frames, pacer.worst_late_ms) == (0, 0.0)


@pytest.mark.asyncio
async def test_lateness_accumulates_across_many_runs():
    clock = _Clock()
    pacer = FramePacer(20.0, clock=clock)
    for _ in range(5):
        pacer.rebase()
        await pacer.wait()
        await _late_frame(pacer, clock)
    assert pacer.late_frames == 5


@pytest.mark.asyncio
async def test_reset_still_does_what_it_always_did():
    """``rebase`` split the deadline from the history, but the original name has
    to keep working — nothing that used it should lose behaviour."""
    clock = _Clock()
    pacer = FramePacer(20.0, clock=clock)
    await pacer.wait()
    await _late_frame(pacer, clock)
    assert pacer.late_frames == 1

    pacer.reset()
    assert (pacer.late_frames, pacer.worst_late_ms) == (0, 0.0)
    clock.advance(5.0)
    await pacer.wait()
    assert pacer.late_frames == 0, "the first frame after a reset is never late"
