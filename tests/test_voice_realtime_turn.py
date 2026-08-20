"""Turn-taking: the piece that decides whether she sounds human."""

from __future__ import annotations

import struct

import pytest

from remedy.telephony.backends.fake import comfort_noise, voiced_pcm
from remedy.telephony.narrowband import frame_bytes
from remedy.voice.realtime.turn import (
    EnergyTurnDetector,
    SmartTurnDetector,
    TurnEvent,
    make_detector,
    smart_turn_model_path,
)

FRAME = frame_bytes(8000)


def _frames(pcm: bytes) -> list[bytes]:
    return [pcm[i : i + FRAME].ljust(FRAME, b"\x00") for i in range(0, len(pcm), FRAME)]


def _quiet(n: int) -> list[bytes]:
    return [comfort_noise(FRAME, seed=i) for i in range(n)]


class _Inline:
    """An executor that runs the job on the spot: verdicts are deterministic,
    and land on the very next frame, which is the earliest the real thread
    could manage anyway."""

    def __init__(self) -> None:
        self.submitted = 0

    def submit(self, fn, *args):
        from concurrent.futures import Future

        self.submitted += 1
        fut: Future = Future()
        try:
            fut.set_result(fn(*args))
        except BaseException as exc:  # noqa: BLE001 — mirrors a real executor
            fut.set_exception(exc)
        return fut


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

    detector = _Unfinished(
        model_path="x", executor=_Inline(), energy=EnergyTurnDetector(hangover_ms=200.0)
    )
    frames = _quiet(5) + _frames(voiced_pcm(400)) + _quiet(15)
    assert [e for _, e in _feed(detector, frames)] == [TurnEvent.SPEECH_START]


def test_smart_turn_endpoints_when_the_model_says_finished():
    class _Finished(SmartTurnDetector):
        @property
        def available(self) -> bool:
            return True

        def score(self, pcm: bytes) -> float:
            return 0.95

    detector = _Finished(
        model_path="x", executor=_Inline(), energy=EnergyTurnDetector(hangover_ms=200.0)
    )
    frames = _quiet(5) + _frames(voiced_pcm(400)) + _quiet(15)
    assert TurnEvent.ENDPOINT in [e for _, e in _feed(detector, frames)]


def test_make_detector_always_returns_something_usable():
    assert isinstance(make_detector(""), EnergyTurnDetector)
    assert isinstance(make_detector("/nonexistent/model.onnx"), EnergyTurnDetector)


class _NeverFinished(SmartTurnDetector):
    """The model insists they are mid-sentence, every time it is asked."""

    def __init__(self, **kw) -> None:
        kw.setdefault("executor", _Inline())
        super().__init__(**kw)
        self.asked = 0

    @property
    def available(self) -> bool:
        return True

    def score(self, pcm: bytes) -> float:
        self.asked += 1
        return 0.0


def test_a_long_sentence_still_reaches_the_model():
    """``max_wait_ms`` used to be checked against the length of the utterance.

    Any sentence past ~2.4 s — most real ones, and exactly the trailing-off case
    this class exists for — skipped the model entirely and silently degraded to
    energy endpointing.
    """
    detector = _NeverFinished(
        model_path="x", max_wait_ms=2400.0, energy=EnergyTurnDetector(hangover_ms=200.0)
    )
    # Four seconds of speech: longer than max_wait_ms, but not a pause in sight.
    frames = _quiet(30) + _frames(voiced_pcm(4000)) + _quiet(15)
    events = [e for _, e in _feed(detector, frames)]
    assert detector.asked >= 1, "the model was never consulted"
    assert TurnEvent.ENDPOINT not in events


def test_the_model_can_only_hold_the_turn_for_max_wait():
    """It bounds the veto, so a model stuck on 'not yet' cannot hold the line."""
    detector = _NeverFinished(
        model_path="x", max_wait_ms=600.0, energy=EnergyTurnDetector(hangover_ms=200.0)
    )
    frames = _quiet(30) + _frames(voiced_pcm(4000)) + _quiet(80)
    events = [e for _, e in _feed(detector, frames)]
    assert TurnEvent.ENDPOINT in events


def test_the_buffer_kept_for_the_model_stays_bounded():
    """A held pause no longer caps this, so the window has to."""
    detector = _NeverFinished(
        model_path="x", window_ms=1000.0, energy=EnergyTurnDetector(hangover_ms=200.0)
    )
    _feed(detector, _quiet(30) + _frames(voiced_pcm(6000)))
    assert len(detector._buffer) <= detector._window_bytes


class _FakeInput:
    def __init__(self, name: str, shape: list) -> None:
        self.name = name
        self.shape = shape


class _FakeSession:
    """Stands in for onnxruntime.InferenceSession — the model is not shipped."""

    def __init__(self, shape: list, output) -> None:
        self._shape = shape
        self._output = output
        self.fed = None

    def get_inputs(self):
        return [_FakeInput("input_values", self._shape)]

    def run(self, _outputs, feed):
        self.fed = feed["input_values"]
        return [self._output]


def _wired(shape, output) -> SmartTurnDetector:
    d = SmartTurnDetector(model_path="pinned.onnx")
    d._tried = True  # skip the lazy import; the session is injected
    d._session = _FakeSession(shape, output)
    return d


def test_the_model_gets_the_window_it_asked_for():
    """``score`` used to raise NotImplementedError, and ``_complete`` caught it
    and returned True — so a loaded model always said "finished" and semantic
    endpointing silently collapsed into the silence timer it exists to beat."""
    import numpy as np

    d = _wired(["batch", 16000 * 8], np.array([[0.2, 2.4]], dtype=np.float32))
    d.score(voiced_pcm(3000, 8000))

    fed = d._session.fed
    assert fed.shape == (1, 16000 * 8), "the model was handed the wrong window"
    assert fed.dtype == np.float32
    assert float(fed.min()) >= -1.0, "not normalised"
    assert float(fed.max()) <= 1.0, "not normalised"


def test_short_audio_is_left_padded_so_the_end_stays_at_the_end():
    """The model judges the *end* of a turn; padding must not push it inward."""
    import numpy as np

    d = _wired(["batch", 16000], np.array([[0.0, 0.0]], dtype=np.float32))
    d.score(voiced_pcm(100, 8000))
    fed = d._session.fed[0]
    assert fed.shape == (16000,)
    assert float(np.abs(fed[:8000]).max()) == 0.0, "padding landed on the wrong side"
    assert float(np.abs(fed[-1000:]).max()) > 0.0, "the speech was padded away"


def test_long_audio_keeps_the_tail():
    import numpy as np

    d = _wired(["batch", 8000], np.array([[0.0, 0.0]], dtype=np.float32))
    d.score(voiced_pcm(4000, 8000))
    assert d._session.fed.shape == (1, 8000)
    assert float(np.abs(d._session.fed).max()) > 0.0


@pytest.mark.parametrize(
    ("output", "expect"),
    [
        ([[0.2, 2.4]], 0.900),          # two logits: softmax, index 1 is "complete"
        ([[2.4, 0.2]], 0.100),
        ([0.83], 0.830),                # a single value already a probability
        ([[-2.0]], 0.119),              # a single raw logit: sigmoid
        ([[2.0]], 0.881),
    ],
)
def test_every_head_shape_becomes_a_probability(output, expect):
    """smart-turn has shipped with a 2-logit classifier and a single sigmoid;
    a model pinned later must not need a code change here."""
    import numpy as np

    d = _wired(["batch", 4000], np.array(output, dtype=np.float32))
    assert d.score(voiced_pcm(500, 8000)) == pytest.approx(expect, abs=0.002)


def test_a_mel_model_gets_log_mel_features_not_raw_samples():
    """smart-turn v3 takes ``(1, 80, 800)`` log-mel. Reshaping raw samples to
    ``(1, N)`` did not fail against it — it scored noise, every turn."""
    import numpy as np

    d = _wired(["batch", 80, 800], np.array([[0.9]], dtype=np.float32))
    d.score(voiced_pcm(3000, 8000))
    fed = d._session.fed
    assert fed.shape == (1, 80, 800)
    assert fed.dtype == np.float32
    # Whisper-style normalisation: the floor sits 8 dB under the peak, at -1.
    assert float(fed.max()) < 1.5
    assert float(fed.min()) == pytest.approx(float(fed.max()) - 2.0)
    # 3 s of audio in an 8 s window: the left (padded) part is the silence
    # floor, the right part carries the speech.
    assert float(fed[0, :, :400].max()) < float(fed[0, :, 500:].max())


def test_log_mel_matches_whispers_recipe():
    """Checked against ``transformers.WhisperFeatureExtractor`` when it is
    installed (agreement to ~1e-6); otherwise the structural properties."""
    import numpy as np

    from remedy.voice.realtime.turn import log_mel, mel_filterbank

    rng = np.random.default_rng(0)
    x = (rng.standard_normal(16000 * 2) * 0.1).astype(np.float32)
    mine = log_mel(x)
    assert mine.shape == (80, 200)  # 10 ms hop, final frame dropped
    fb = mel_filterbank()
    assert fb.shape == (80, 201)
    assert np.all(fb >= 0)
    try:
        from transformers import WhisperFeatureExtractor
    except ImportError:
        pytest.skip("transformers not installed; structural checks only")
    fe = WhisperFeatureExtractor(feature_size=80)
    ref = fe(x, sampling_rate=16000, return_tensors="np", padding=False)["input_features"][0]
    assert np.abs(ref[:, : mine.shape[1]] - mine).max() < 1e-4


def test_a_mel_model_with_a_free_frame_axis_gets_the_configured_window():
    import numpy as np

    d = _wired(["batch", 80, "frames"], np.array([[0.9]], dtype=np.float32))
    d.window_ms = 2000.0
    d.score(voiced_pcm(500, 8000))
    assert d._session.fed.shape == (1, 80, 200)


def test_a_model_with_an_input_rank_we_cannot_build_is_refused_not_fed(monkeypatch, caplog):
    import logging
    import sys
    import types

    class _Session:
        def __init__(self, path, providers=None) -> None:
            pass

        def get_inputs(self):
            return [_FakeInput("x", ["batch", 1, 80, 800])]

        def run(self, *a, **k):
            pytest.fail("a model with an unsupported input shape was run")

    monkeypatch.setitem(sys.modules, "onnxruntime", types.SimpleNamespace(InferenceSession=_Session))
    d = SmartTurnDetector(model_path="weird.onnx")
    with caplog.at_level(logging.WARNING, logger="remedy.voice.realtime.turn"):
        assert d.available is False
    assert "cannot produce" in d.unavailable_reason
    assert any("cannot produce" in r.getMessage() for r in caplog.records)


def test_a_model_with_no_fixed_window_falls_back_to_the_configured_one():
    d = _wired(["batch", "sequence"], [[0.0, 1.0]])
    d.window_ms = 2000.0
    d.score(voiced_pcm(500, 8000))
    assert d._session.fed.shape == (1, int(16000 * 2.0))


def test_scoring_without_a_model_raises_rather_than_guessing():
    d = SmartTurnDetector(model_path="")
    with pytest.raises(RuntimeError):
        d.score(voiced_pcm(500, 8000))


def test_a_model_that_misbehaves_never_hangs_the_call(caplog):
    """``_complete`` still falls back to "finished" — a wobble must not leave
    the far end waiting for someone who already stopped talking. But it used
    to do so silently, at debug level, with ``available`` still True: every
    turn paid for a forward pass that always failed, and nobody was told."""
    import logging

    class _Broken(SmartTurnDetector):
        def score(self, pcm: bytes) -> float:
            raise RuntimeError("inference exploded")

    d = _Broken(model_path="x")
    d._tried = True
    d._session = object()
    assert d.available
    with caplog.at_level(logging.WARNING, logger="remedy.voice.realtime.turn"):
        assert d._complete(voiced_pcm(500, 8000)) is True
    assert not d.available, "a failing model must be retired, not retried every turn"
    assert "inference exploded" in d.unavailable_reason
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "inference exploded" in warnings[0].getMessage()


def test_after_an_inference_failure_the_detector_is_an_explicit_energy_timer():
    """The caller sees ``available=False`` and the next pause is not sent to
    the model at all — the fallback is stated, not hidden."""

    class _Broken(SmartTurnDetector):
        def __init__(self, **kw) -> None:
            super().__init__(**kw)
            self.asked = 0

        def score(self, pcm: bytes) -> float:
            self.asked += 1
            raise RuntimeError("inference exploded")

    d = _Broken(model_path="x", executor=_Inline(), energy=EnergyTurnDetector(hangover_ms=200.0))
    d._tried = True
    d._session = object()
    frames = _quiet(5) + _frames(voiced_pcm(400)) + _quiet(15)
    events = [e for _, e in _feed(d, frames)]
    assert TurnEvent.ENDPOINT in events
    assert d.asked == 1
    assert not d.available
    # Second turn: never asked again.
    events = [e for _, e in _feed(d, _frames(voiced_pcm(400)) + _quiet(15), start=1.0)]
    assert TurnEvent.ENDPOINT in events
    assert d.asked == 1


def test_inference_runs_off_the_audio_loop():
    """``feed`` is called every 20 ms; a forward pass is ~80 ms. Running it
    inline stalled four frames at every pause, and barge-in with them."""
    import threading
    import time

    started = threading.Event()
    release = threading.Event()

    class _Slow(SmartTurnDetector):
        @property
        def available(self) -> bool:
            return True

        def score(self, pcm: bytes) -> float:
            started.set()
            release.wait(5.0)
            return 0.95

    d = _Slow(model_path="x", energy=EnergyTurnDetector(hangover_ms=200.0))
    frames = _quiet(5) + _frames(voiced_pcm(400)) + _quiet(10)
    t0 = time.perf_counter()
    events = [e for _, e in _feed(d, frames)]
    elapsed = time.perf_counter() - t0
    assert started.wait(2.0), "the model was never consulted"
    assert TurnEvent.ENDPOINT not in events
    assert elapsed < 0.5, f"feed blocked on inference for {elapsed:.2f}s"
    assert d._pending is not None and not d._pending.done()
    # The verdict is picked up on the first frame after it lands.
    release.set()
    d._pending.result(timeout=2.0)
    assert d.feed(_quiet(1)[0], 1.0) is TurnEvent.ENDPOINT
    assert d._pending is None
    d.executor.shutdown(wait=True)


def test_a_verdict_about_a_pause_they_talked_through_is_dropped():
    """They resumed while the model was still thinking about the pause. Its
    answer is about something that is over, and must not end the new turn."""
    from concurrent.futures import Future

    class _Manual:
        def __init__(self) -> None:
            self.futures: list[Future] = []

        def submit(self, fn, *args):
            fut: Future = Future()
            self.futures.append(fut)
            return fut

    class _Always(SmartTurnDetector):
        @property
        def available(self) -> bool:
            return True

    ex = _Manual()
    d = _Always(model_path="x", executor=ex, energy=EnergyTurnDetector(hangover_ms=200.0))
    _feed(d, _quiet(5) + _frames(voiced_pcm(400)) + _quiet(10))
    assert len(ex.futures) == 1 and d._pending is ex.futures[0]
    events = [e for _, e in _feed(d, _frames(voiced_pcm(200)), start=1.0)]  # they resume
    assert d._pending is None
    ex.futures[0].set_result(True)  # "finished" — about the old pause
    assert d.feed(_frames(voiced_pcm(20))[0], 2.0) is None
    assert TurnEvent.ENDPOINT not in events


def test_the_hold_clock_stops_when_they_resume_talking():
    """``_held_ms`` kept counting through resumed speech, because energy never
    re-fires onset while we are holding it in the speaking state. A talker
    who paused once then spoke for a while had their *next* pause cut short:
    the veto budget was already spent on their own words."""
    detector = _NeverFinished(
        model_path="x", max_wait_ms=600.0, energy=EnergyTurnDetector(hangover_ms=200.0)
    )
    # Pause 1: energy fires at 200 ms, the model says "not done", we hold.
    _feed(detector, _quiet(30) + _frames(voiced_pcm(1000)) + _quiet(12))
    assert detector._holding
    assert detector._held_ms > 0
    # They resume, for longer than max_wait_ms.
    _feed(detector, _frames(voiced_pcm(1000)), start=2.0)
    assert not detector._holding
    assert detector._held_ms == 0.0, "time spent talking was charged to the model's veto"
    # Pause 2: the model must still be allowed to hold for the full budget.
    before = detector.asked
    events = [e for _, e in _feed(detector, _quiet(12), start=3.0)]
    assert detector.asked == before + 1
    assert TurnEvent.ENDPOINT not in events


def test_the_verdict_respects_the_threshold():
    import numpy as np

    high = _wired(["batch", 4000], np.array([[0.0, 5.0]], dtype=np.float32))
    high.completion_threshold = 0.55
    assert high._complete(voiced_pcm(500, 8000)) is True

    low = _wired(["batch", 4000], np.array([[5.0, 0.0]], dtype=np.float32))
    low.completion_threshold = 0.55
    assert low._complete(voiced_pcm(500, 8000)) is False


def test_a_missing_model_is_the_normal_case_not_a_failure(tmp_path):
    """Nothing telephony-related ships with Remedy, so no model is the default
    state. Energy endpointing is the floor and must always be handed back."""
    assert smart_turn_model_path(tmp_path) == ""
    assert isinstance(make_detector(home=tmp_path), EnergyTurnDetector)


def test_a_fetched_model_is_found_without_being_told_where(tmp_path):
    """``make_detector`` needed an explicit path and nothing in the tree ever
    passed one, so the semantic detector could never be reached at all."""
    root = tmp_path / "voice" / "models" / "smart-turn"
    root.mkdir(parents=True)
    pinned = root / "smart-turn-v2.onnx"
    pinned.write_bytes(b"x")
    assert smart_turn_model_path(tmp_path) == str(pinned)


def test_the_newest_pin_wins_when_a_repin_left_two(tmp_path):
    import os
    import time

    root = tmp_path / "voice" / "models" / "smart-turn"
    root.mkdir(parents=True)
    old = root / "smart-turn-v2.onnx"
    new = root / "smart-turn-v3.onnx"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    now = time.time()
    os.utime(old, (now - 100, now - 100))
    os.utime(new, (now, now))
    assert smart_turn_model_path(tmp_path) == str(new)


def test_a_model_file_that_will_not_load_still_leaves_a_working_detector(tmp_path):
    """Degrade with a stated reason, never leave the caller without a detector."""
    root = tmp_path / "voice" / "models" / "smart-turn"
    root.mkdir(parents=True)
    (root / "broken.onnx").write_bytes(b"definitely not an onnx graph")
    detector = make_detector(home=tmp_path)
    assert isinstance(detector, EnergyTurnDetector)


def test_an_explicit_path_still_wins():
    detector = make_detector("/nonexistent/pinned.onnx")
    assert isinstance(detector, EnergyTurnDetector)
