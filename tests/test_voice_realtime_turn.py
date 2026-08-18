"""Turn-taking: the piece that decides whether she sounds human."""

from __future__ import annotations

import struct

from remedy.telephony.backends.fake import comfort_noise, voiced_pcm
from remedy.telephony.narrowband import frame_bytes
from remedy.voice.realtime.turn import (
    EnergyTurnDetector,
    SmartTurnDetector,
    TurnEvent,
    make_detector,
)

FRAME = frame_bytes(8000)


def _frames(pcm: bytes) -> list[bytes]:
    return [pcm[i : i + FRAME].ljust(FRAME, b"\x00") for i in range(0, len(pcm), FRAME)]


def _quiet(n: int) -> list[bytes]:
    return [comfort_noise(FRAME, seed=i) for i in range(n)]


def _feed(detector, frames: list[bytes], start: float = 0.0) -> list[tuple[float, TurnEvent]]:
    events = []
    t = start
    for frame in frames:
        event = detector.feed(frame, t)
        if event is not None:
            events.append((t, event))
        t += 0.02
    return events


def test_comfort_noise_does_not_trip_the_detector():
    """Real lines are never digitally silent; hiss must not read as speech."""
    detector = EnergyTurnDetector()
    assert _feed(detector, _quiet(100)) == []
    assert not detector.speaking


def test_speech_start_then_endpoint_after_hangover():
    detector = EnergyTurnDetector(hangover_ms=300.0)
    frames = _quiet(10) + _frames(voiced_pcm(600)) + _quiet(40)
    events = [e for _, e in _feed(detector, frames)]
    assert events == [TurnEvent.SPEECH_START, TurnEvent.ENDPOINT]


def test_endpoint_waits_the_full_hangover():
    """The hangover is pure added latency, so it must be exactly what we asked
    for — this is the term that dominates time-to-answer (docs/TELEPHONY.md)."""
    detector = EnergyTurnDetector(hangover_ms=400.0)
    lead, speech = 5, _frames(voiced_pcm(400))
    events = _feed(detector, _quiet(lead) + speech + _quiet(30))
    # Speech really ends after the lead-in plus the voiced frames. The onset
    # *event* lags that start by onset_frames, so it is the wrong origin to
    # measure from.
    speech_ended = (lead + len(speech)) * 0.02
    end = next(t for t, e in events if e is TurnEvent.ENDPOINT)
    assert 360 <= (end - speech_ended) * 1000 <= 440


def test_short_hesitation_does_not_end_the_turn():
    """"...date of birth?" with a breath in the middle is one turn, not two."""
    detector = EnergyTurnDetector(hangover_ms=700.0)
    frames = (
        _quiet(5)
        + _frames(voiced_pcm(500))
        + _quiet(20)  # 400 ms pause, under the hangover
        + _frames(voiced_pcm(500))
        + _quiet(50)
    )
    events = [e for _, e in _feed(detector, frames)]
    assert events == [TurnEvent.SPEECH_START, TurnEvent.ENDPOINT]


def test_onset_requires_consecutive_voiced_frames():
    """A single loud click is not someone starting to talk."""
    detector = EnergyTurnDetector(onset_frames=3)
    click = struct.pack(f"<{FRAME // 2}h", *([9000] * (FRAME // 2)))
    assert _feed(detector, [comfort_noise(FRAME), click, comfort_noise(FRAME)]) == []


def test_threshold_floats_above_the_noise_floor():
    detector = EnergyTurnDetector()
    loud_line = [comfort_noise(FRAME, seed=i, level=0.02) for i in range(60)]
    _feed(detector, loud_line)
    assert detector.threshold > 0.02
    assert not detector.speaking


def test_call_that_opens_mid_speech_does_not_deafen_the_detector():
    """Regression: with almost no history the floor was computed from the first
    frame. If that frame is speech — they answer mid-word, an IVR starts on
    connect — the floor latched onto speech energy and every later utterance
    read as silence, so she stopped hearing people for the rest of the call."""
    detector = EnergyTurnDetector(hangover_ms=300.0)
    # No lead-in silence at all: the very first frame is someone talking.
    events = _feed(detector, _frames(voiced_pcm(400)) + _quiet(30))
    assert [e for _, e in events] == [TurnEvent.SPEECH_START, TurnEvent.ENDPOINT]
    # And the threshold must still be low enough to hear a quiet talker.
    assert detector.threshold < 0.04


def test_noise_floor_is_capped():
    """A floor this high means we measured speech, not hiss."""
    detector = EnergyTurnDetector()
    _feed(detector, _frames(voiced_pcm(2000)))
    assert detector._floor <= detector.max_floor


def test_reset_clears_speaking_state():
    detector = EnergyTurnDetector()
    _feed(detector, _frames(voiced_pcm(300)))
    assert detector.speaking
    detector.reset()
    assert not detector.speaking


def test_smart_turn_reports_why_it_is_unavailable_instead_of_raising():
    """A missing model is a degraded mode, never an exception on a live call."""
    detector = SmartTurnDetector(model_path="")
    assert not detector.available
    assert "no smart-turn model" in detector.unavailable_reason


def test_smart_turn_falls_back_to_energy_endpointing():
    detector = SmartTurnDetector(model_path="", energy=EnergyTurnDetector(hangover_ms=200.0))
    frames = _quiet(5) + _frames(voiced_pcm(400)) + _quiet(30)
    assert [e for _, e in _feed(detector, frames)] == [
        TurnEvent.SPEECH_START,
        TurnEvent.ENDPOINT,
    ]


def test_smart_turn_keeps_waiting_when_the_model_says_mid_sentence():
    """The model's whole value: overriding a silence timer that fired early."""

    class _Unfinished(SmartTurnDetector):
        @property
        def available(self) -> bool:
            return True

        def score(self, pcm: bytes) -> float:
            return 0.1  # "they are not done"

    detector = _Unfinished(model_path="x", energy=EnergyTurnDetector(hangover_ms=200.0))
    frames = _quiet(5) + _frames(voiced_pcm(400)) + _quiet(15)
    assert [e for _, e in _feed(detector, frames)] == [TurnEvent.SPEECH_START]


def test_smart_turn_endpoints_when_the_model_says_finished():
    class _Finished(SmartTurnDetector):
        @property
        def available(self) -> bool:
            return True

        def score(self, pcm: bytes) -> float:
            return 0.95

    detector = _Finished(model_path="x", energy=EnergyTurnDetector(hangover_ms=200.0))
    frames = _quiet(5) + _frames(voiced_pcm(400)) + _quiet(15)
    assert TurnEvent.ENDPOINT in [e for _, e in _feed(detector, frames)]


def test_make_detector_always_returns_something_usable():
    assert isinstance(make_detector(""), EnergyTurnDetector)
    assert isinstance(make_detector("/nonexistent/model.onnx"), EnergyTurnDetector)
