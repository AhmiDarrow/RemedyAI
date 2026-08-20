"""Frame timing — the unglamorous reason she does not sound like a robot.

Measured on the dev host (Windows 11, i9-10900K, Python 3.13, ProactorEventLoop):

    asyncio.sleep(20 ms)                 median 31.3 ms   max 33.3 ms
    asyncio.sleep(20 ms) + 1 ms timer    median 20.6 ms   max 21.9 ms

Windows' default timer resolution is ~15.6 ms, so a 20 ms audio frame — the RTP
convention every telephony stack is built on — *cannot be represented*. Left
alone, playout runs 56% slow, frames arrive late, the far end hears stutter and
gaps, and no amount of synthesis quality recovers it.

Two fixes, both here:

* ``precise_timing()`` raises the system timer resolution for the duration of a
  call (no-op off Windows, where 20 ms sleeps are already fine).
* ``FramePacer`` schedules against absolute deadlines instead of sleeping a
  fixed amount per frame, so error does not accumulate over a long call.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import sys
import time
from collections.abc import Iterator

from remedy.telephony.narrowband import FRAME_MS

logger = logging.getLogger(__name__)

_TIMER_MS = 1


@contextlib.contextmanager
def precise_timing(period_ms: int = _TIMER_MS) -> Iterator[bool]:
    """Raise the OS timer resolution while a call is up.

    Yields True when the resolution was actually raised. Failure is not an
    error — the call still works, it just paces worse, and we say so in the log
    rather than refusing to dial.
    """
    if sys.platform != "win32":
        yield False
        return
    try:
        import ctypes

        winmm = ctypes.WinDLL("winmm")
    except (ImportError, OSError) as exc:
        logger.info("precise timing unavailable (%s); frame pacing will be coarse", exc)
        yield False
        return

    raised = winmm.timeBeginPeriod(period_ms) == 0
    if not raised:
        logger.info("timeBeginPeriod(%d) refused; frame pacing will be coarse", period_ms)
    try:
        yield raised
    finally:
        if raised:
            with contextlib.suppress(Exception):
                winmm.timeEndPeriod(period_ms)


class FramePacer:
    """Absolute-deadline pacing for a stream of fixed-duration frames.

    Sleeping ``frame_ms`` per frame drifts: every overshoot is kept forever. This
    tracks where the next frame *should* land and sleeps the remainder, so a
    twenty-minute call ends as aligned as it started.
    """

    def __init__(self, frame_ms: float = FRAME_MS, clock=time.monotonic) -> None:
        self.frame_ms = float(frame_ms)
        self._clock = clock
        self._next: float | None = None
        self.late_frames = 0
        self.worst_late_ms = 0.0

    def rebase(self) -> None:
        """Start a fresh contiguous run of frames; the next one is never late.

        Deadlines only mean something *inside* a run. Between runs the audio
        source is still working — a synthesizer warming up for the next clause
        owes us nothing on a 20 ms clock — and charging that wait to the pacer
        would report a stall in the engine as a stutter on the wire.

        This does *not* clear ``late_frames``/``worst_late_ms``: playout rebases
        many times a call and the bench reads the counters once at the end, so
        zeroing here would report only the last chunk and hide a call that
        stuttered throughout. Use ``clear_stats()`` to start a new measurement.
        """
        self._next = None

    def clear_stats(self) -> None:
        """Forget the pacing history. Only for starting a new measurement."""
        self.late_frames = 0
        self.worst_late_ms = 0.0

    def reset(self) -> None:
        """Re-base *and* forget the history — the original ``reset``.

        Kept because it was here first and callers may rely on it. Playout wants
        ``rebase()``; a measurement starting from scratch wants this.
        """
        self.rebase()
        self.clear_stats()

    async def wait(self) -> None:
        """Block until the next frame is due."""
        period = self.frame_ms / 1000.0
        now = self._clock()
        if self._next is None:
            self._next = now + period
            return
        delay = self._next - now
        if delay <= 0:
            # We are behind. Count it, and re-base rather than sprinting to
            # catch up — bursting frames at the far end is worse than a late one.
            late_ms = -delay * 1000.0
            self.late_frames += 1
            self.worst_late_ms = max(self.worst_late_ms, late_ms)
            self._next = now + period
            return
        await asyncio.sleep(delay)
        self._next += period


async def timing_is_trustworthy(
    samples: int = 12, tolerance_ms: float = 8.0
) -> tuple[bool, float]:
    """Can this machine hold a 20 ms frame right now?

    Latency cannot be measured on a host that cannot keep time — under heavy
    load a 20 ms sleep can take 30 ms or more, which drifts both sides of a
    simulated conversation and produces overlaps that say nothing about the
    code. Callers use this to skip a measurement rather than report a failure
    they cannot attribute.

    Returns (trustworthy, median overshoot in ms).
    """
    import statistics
    import time as _time

    with precise_timing():
        overshoot: list[float] = []
        for _ in range(samples):
            start = _time.perf_counter()
            await asyncio.sleep(FRAME_MS / 1000.0)
            overshoot.append((_time.perf_counter() - start) * 1000.0 - FRAME_MS)
    median = statistics.median(overshoot)
    return median <= tolerance_ms, median


@contextlib.contextmanager
def audio_priority() -> Iterator[str]:
    """Ask Windows to schedule this thread like a media app, for a call's life.

    Raising the timer resolution fixes *granularity*; it does nothing about
    *contention*. On a busy machine the scheduler can still leave an audio
    thread waiting past its deadline, and the far end hears the gap.

    MMCSS ("Pro Audio") is the mechanism Windows provides for precisely this —
    the same one media players and DAWs use so playback does not glitch when
    the system is loaded. Registration lasts only as long as the call.

    Yields the task name in force, or "" when unavailable. Never raises: worse
    scheduling is a degraded call, not a failed one.
    """
    if sys.platform != "win32":
        yield ""
        return
    try:
        import ctypes
        from ctypes import wintypes

        avrt = ctypes.WinDLL("avrt")
    except (ImportError, OSError) as exc:
        logger.info("audio priority unavailable (%s); playout may glitch under load", exc)
        yield ""
        return

    task_index = wintypes.DWORD(0)
    # Without an explicit restype ctypes truncates the returned HANDLE to a
    # 32-bit signed int, and the revert below would be handed a bad handle.
    avrt.AvSetMmThreadCharacteristicsW.argtypes = [
        wintypes.LPCWSTR,
        ctypes.POINTER(wintypes.DWORD),
    ]
    avrt.AvSetMmThreadCharacteristicsW.restype = wintypes.HANDLE
    # The revert takes that HANDLE back; without argtypes ctypes would marshal
    # a 64-bit value as a C int and raise instead of leaving the class.
    avrt.AvRevertMmThreadCharacteristics.argtypes = [wintypes.HANDLE]
    avrt.AvRevertMmThreadCharacteristics.restype = wintypes.BOOL
    handle = avrt.AvSetMmThreadCharacteristicsW(
        ctypes.c_wchar_p("Pro Audio"), ctypes.byref(task_index)
    )
    if not handle:
        logger.info("could not join the Pro Audio scheduling class; playout may glitch")
        yield ""
        return
    try:
        yield "Pro Audio"
    finally:
        with contextlib.suppress(Exception):
            avrt.AvRevertMmThreadCharacteristics(handle)


@contextlib.contextmanager
def call_timing() -> Iterator[tuple[bool, str]]:
    """Everything a live call needs from the clock, for its duration only.

    Both halves of the problem: ``precise_timing`` for granularity, and
    ``audio_priority`` for contention. Neither is left on afterwards — a raised
    system timer costs power everywhere, and a media-priority thread that is not
    doing audio is just rude to the rest of the machine.
    """
    with precise_timing() as precise, audio_priority() as task:
        yield precise, task
