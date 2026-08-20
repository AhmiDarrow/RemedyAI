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


# ---------------------------------------------------------------------------
# audio_priority — the MMCSS handle must survive the round trip
# ---------------------------------------------------------------------------


class _StrictFunction:
    """ctypes' real rule: with no ``argtypes`` an int is marshalled as a C
    ``int``, and a 64-bit HANDLE value raises ``ArgumentError``."""

    def __init__(self, name: str, impl, calls: list) -> None:
        self.name = name
        self.impl = impl
        self.calls = calls
        self.argtypes = None
        self.restype = None

    def __call__(self, *args):
        import ctypes

        if self.argtypes is None:
            for a in args:
                if isinstance(a, int) and not (-(2**31) <= a < 2**31):
                    raise ctypes.ArgumentError(f"{self.name}: int too long to convert")
        self.calls.append((self.name, args))
        return self.impl(*args)


class _StrictDLL:
    def __init__(self, impls: dict, calls: list) -> None:
        self._fns = {n: _StrictFunction(n, f, calls) for n, f in impls.items()}

    def __getattr__(self, name: str):
        try:
            return self._fns[name]
        except KeyError:
            raise AttributeError(name) from None


_MMCSS_HANDLE = 0x1_0000_0040  # does not fit a C int


def _install_avrt(monkeypatch):
    import ctypes
    import sys

    calls: list = []
    avrt = _StrictDLL(
        {
            "AvSetMmThreadCharacteristicsW": lambda name, pidx: _MMCSS_HANDLE,
            "AvRevertMmThreadCharacteristics": lambda h: 1,
        },
        calls,
    )
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(ctypes, "WinDLL", lambda name, **kw: avrt, raising=False)
    return avrt, calls


def test_audio_priority_actually_leaves_the_pro_audio_class_afterwards(monkeypatch):
    """``AvRevertMmThreadCharacteristics`` had no argtypes, so the 64-bit
    HANDLE from the (correctly typed) set call raised ArgumentError inside a
    ``suppress`` — the thread silently stayed in the media class forever."""
    from remedy.telephony.timing import audio_priority

    _avrt, calls = _install_avrt(monkeypatch)
    with audio_priority() as task:
        assert task == "Pro Audio"
    assert ("AvRevertMmThreadCharacteristics", (_MMCSS_HANDLE,)) in calls


def test_the_avrt_functions_declare_handle_argtypes(monkeypatch):
    from ctypes import wintypes

    from remedy.telephony.timing import audio_priority

    avrt, _calls = _install_avrt(monkeypatch)
    with audio_priority():
        pass
    assert avrt.AvRevertMmThreadCharacteristics.argtypes == [wintypes.HANDLE]
    assert avrt.AvRevertMmThreadCharacteristics.restype is wintypes.BOOL
    assert avrt.AvSetMmThreadCharacteristicsW.restype is wintypes.HANDLE
    assert avrt.AvSetMmThreadCharacteristicsW.argtypes is not None
