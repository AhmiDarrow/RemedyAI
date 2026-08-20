"""Knowing when it is her turn.

This is the anti-robotic piece. Everything else — better synthesis, a bigger
model — moves the needle less than getting turn-taking right, because a human
listener forgives a slightly synthetic timbre and never forgives being talked
over or left hanging.

Two detectors, one interface:

* ``EnergyTurnDetector`` — adaptive noise floor plus a hangover timer. Always
  available, stdlib only, and honest about what it is: a silence timer. It waits
  the same 700 ms after "yes" as after "so what I'd like to ask about is —".
* ``SmartTurnDetector`` — pipecat's smart-turn (BSD-2), which judges from the
  waveform whether the *sentence* finished, not merely whether sound stopped.
  Lazily loaded, and it degrades to energy with a stated reason rather than
  failing a live call.
"""

from __future__ import annotations

import logging
from collections import deque
from concurrent.futures import Future, ThreadPoolExecutor
from contextlib import suppress
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

from remedy.telephony.narrowband import FRAME_MS, PHONE_RATE, resample, rms

logger = logging.getLogger(__name__)


class TurnEvent(StrEnum):
    #: The far end started talking (also the barge-in trigger).
    SPEECH_START = "speech_start"
    #: They finished a turn — she may answer.
    ENDPOINT = "endpoint"


class TurnDetector(Protocol):
    def feed(self, pcm: bytes, at: float) -> TurnEvent | None: ...

    def reset(self) -> None: ...

    @property
    def speaking(self) -> bool: ...

    @property
    def voiced(self) -> bool:
        """Was the last fed frame sound, by *this* detector's threshold?

        Callers must not run their own energy test. The pipeline used to, and
        the two disagreed on quiet frames: it read a soft syllable as "they
        resumed" while the detector read the same frame as "they stopped", so
        one turn was discarded and another committed on the same 20 ms of
        audio. One threshold, one answer.
        """
        ...


@dataclass(slots=True)
class EnergyTurnDetector:
    """Silence-based endpointing with an adaptive floor.

    Phone lines carry comfort noise, and a fixed threshold either clips quiet
    talkers or triggers on hiss. The floor tracks the quietest recent frames and
    the trigger sits a fixed margin above it.
    """

    #: Consecutive voiced frames before we believe speech started.
    onset_frames: int = 3
    #: Silence held before we call the turn over.
    hangover_ms: float = 700.0
    #: How far above the noise floor counts as voice.
    margin: float = 0.018
    #: Absolute floor so pure digital silence never trips the detector.
    min_threshold: float = 0.012
    #: Frames of history before the adaptive floor is trusted. Without this the
    #: floor is computed from almost no data, and a call that opens while the
    #: far end is already talking — they answer mid-word, an IVR starts on
    #: connect — latches the floor onto *speech* energy. The detector then goes
    #: half-deaf for the rest of the call and reads new speech as silence.
    warmup_frames: int = 25
    #: A noise floor this high is a broken measurement, not a noisy line.
    max_floor: float = 0.05
    frame_ms: float = FRAME_MS

    _floor: float = field(default=0.0, init=False)
    _voiced_run: int = field(default=0, init=False)
    _silent_ms: float = field(default=0.0, init=False)
    _speaking: bool = field(default=False, init=False)
    _voiced: bool = field(default=False, init=False)
    _recent: deque = field(default_factory=lambda: deque(maxlen=50), init=False)

    @property
    def speaking(self) -> bool:
        return self._speaking

    @property
    def voiced(self) -> bool:
        return self._voiced

    @property
    def threshold(self) -> float:
        return max(self.min_threshold, self._floor + self.margin)

    def reset(self) -> None:
        self._voiced_run = 0
        self._silent_ms = 0.0
        self._speaking = False
        self._voiced = False

    def feed(self, pcm: bytes, at: float) -> TurnEvent | None:
        level = rms(pcm)
        self._recent.append(level)
        if not self._speaking and len(self._recent) >= self.warmup_frames:
            # Track the floor only while nobody is talking, only once there is
            # enough history to tell hiss from a voice, and never above a level
            # that would mean we measured speech by mistake.
            quiet = sorted(self._recent)[: max(4, len(self._recent) // 4)]
            self._floor = min(self.max_floor, sum(quiet) / len(quiet))

        self._voiced = level >= self.threshold
        if self._voiced:
            self._silent_ms = 0.0
            self._voiced_run += 1
            if not self._speaking and self._voiced_run >= self.onset_frames:
                self._speaking = True
                return TurnEvent.SPEECH_START
        else:
            self._voiced_run = 0
            if self._speaking:
                self._silent_ms += self.frame_ms
                if self._silent_ms >= self.hangover_ms:
                    self._speaking = False
                    self._silent_ms = 0.0
                    return TurnEvent.ENDPOINT
        return None


@dataclass(slots=True)
class SmartTurnDetector:
    """Semantic endpointing (pipecat smart-turn v2/v3, BSD-2), when present.

    Runs the energy detector underneath for onset and for the hard timeout, and
    asks the model only at the moment energy *thinks* the turn ended — which is
    where the model earns its keep: it can say "no, they are mid-sentence, keep
    waiting", or "that was a complete thought, answer now" well before the
    hangover would have expired.
    """

    model_path: str = ""
    #: Below this the model's "unfinished" verdict is trusted and we keep waiting.
    completion_threshold: float = 0.55
    #: Never keep waiting more than this *past the first endpoint the model
    #: overrules*. It bounds the model's veto, not the length of the utterance:
    #: measured against speech duration instead, any sentence longer than a
    #: couple of seconds — which is most of them, and exactly the trailing-off
    #: case this class exists for — would skip the model entirely and silently
    #: degrade to energy endpointing.
    max_wait_ms: float = 2400.0
    #: Trailing audio kept for the model. It only ever judges the end of a turn,
    #: and a held pause no longer bounds this, so a long monologue must not be
    #: buffered whole.
    window_ms: float = 8000.0
    #: Line rate, for sizing that window in bytes.
    sample_rate: int = PHONE_RATE
    #: What the model was trained on. smart-turn is Wav2Vec2-based, so 16 kHz.
    #: The line is 8 kHz, so every window is resampled before it goes in.
    model_sample_rate: int = 16000
    energy: EnergyTurnDetector = field(default_factory=EnergyTurnDetector)
    #: Where inference runs. ``feed`` is called from the audio loop every 20 ms
    #: and a forward pass takes ~80 ms, so it must not run inline: four frames
    #: would queue behind it and barge-in would land late. Anything with a
    #: ``submit(fn, *args) -> Future`` works; tests inject a synchronous one.
    executor: Any = None

    _session: Any = field(default=None, init=False)
    _tried: bool = field(default=False, init=False)
    _unavailable: str = field(default="", init=False)
    _buffer: bytearray = field(default_factory=bytearray, init=False)
    _held_ms: float = field(default=0.0, init=False)
    _holding: bool = field(default=False, init=False)
    #: The verdict we are waiting on, if the model is thinking right now.
    _pending: Future | None = field(default=None, init=False)

    @property
    def speaking(self) -> bool:
        return self.energy.speaking

    @property
    def voiced(self) -> bool:
        return self.energy.voiced

    @property
    def available(self) -> bool:
        self._ensure()
        return self._session is not None

    @property
    def unavailable_reason(self) -> str:
        self._ensure()
        return self._unavailable

    def _ensure(self) -> None:
        """Lazy load. A missing model is a degraded mode, never an exception."""
        if self._tried:
            return
        self._tried = True
        if not self.model_path:
            self._unavailable = "no smart-turn model configured"
            return
        try:
            import onnxruntime
        except ImportError:
            self._unavailable = "onnxruntime not installed"
            return
        try:
            self._session = onnxruntime.InferenceSession(
                self.model_path, providers=["CPUExecutionProvider"]
            )
        except Exception as exc:  # noqa: BLE001 — any load failure degrades
            self._unavailable = f"smart-turn model failed to load: {exc}"
            self._session = None
            return
        shape = self._input_shape()
        if len(shape) not in (2, 3):
            self._unavailable = (
                f"smart-turn model wants input shape {shape}, which this build "
                "cannot produce (expected raw (1, N) or log-mel (1, 80, frames))"
            )
            logger.warning("turn-taking: %s; using energy endpointing", self._unavailable)
            self._session = None

    def reset(self) -> None:
        self.energy.reset()
        self._buffer.clear()
        self._release()

    def _release(self) -> None:
        """Stop counting against ``max_wait_ms``; the model is not holding us.

        Any verdict still in flight is about a pause that is over, so it is
        dropped unread rather than allowed to end a turn that resumed.
        """
        self._held_ms = 0.0
        self._holding = False
        self._pending = None

    def _endpoint(self) -> TurnEvent:
        self._buffer.clear()
        self._release()
        return TurnEvent.ENDPOINT

    @property
    def _window_bytes(self) -> int:
        return max(1, int(self.window_ms * self.sample_rate / 1000.0)) * 2

    def _submit(self, pcm: bytes) -> Future:
        if self.executor is None:
            self.executor = ThreadPoolExecutor(
                max_workers=1, thread_name_prefix="remedy-smart-turn"
            )
        return self.executor.submit(self._complete, pcm)

    def feed(self, pcm: bytes, at: float) -> TurnEvent | None:
        event = self.energy.feed(pcm, at)
        if self.energy.speaking or event is TurnEvent.ENDPOINT:
            self._buffer.extend(pcm)
            if len(self._buffer) > self._window_bytes:
                del self._buffer[: len(self._buffer) - self._window_bytes]
        if self._holding and self.energy.voiced:
            # They resumed. Energy never re-fires onset here — we forced it to
            # stay in the speaking state to hold the turn — so this is the only
            # place that can notice, and the clock must stop: otherwise time
            # they spent *talking* counts against how long the model may hold
            # their next pause.
            self._release()
        if self._holding:
            # Only the time we have spent overruling energy counts here.
            self._held_ms += self.energy.frame_ms
        if event is TurnEvent.SPEECH_START:
            self._buffer.clear()
            self._buffer.extend(pcm)
            self._release()
            return event
        if self._pending is not None:
            # A verdict may have landed since the last frame.
            if self._held_ms >= self.max_wait_ms:
                return self._endpoint()
            if not self._pending.done():
                return None
            finished, self._pending = self._pending.result(), None
            if finished:
                return self._endpoint()
            # "Still mid-sentence": keep holding; energy will ask again after
            # its next hangover, and ``max_wait_ms`` bounds the whole wait.
            return None
        if event is not TurnEvent.ENDPOINT:
            return None
        # Energy says the turn ended. Ask the model whether the thought did.
        if self._held_ms >= self.max_wait_ms or not self.available:
            return self._endpoint()
        # The answer arrives on a later frame; until then keep listening, and
        # start the clock on how long we are willing to be held there.
        self._pending = self._submit(bytes(self._buffer))
        self._holding = True
        self.energy._speaking = True  # noqa: SLF001 — same conceptual object
        self.energy._silent_ms = 0.0  # noqa: SLF001
        return None

    def _complete(self, pcm: bytes) -> bool:
        """Did they finish? Runs on the worker thread.

        A failure is not a wobble to retry every turn: it is logged once, at a
        level someone will see, and the model is retired for the rest of the
        detector's life so the caller falls back to the timer *knowingly*.
        """
        try:
            score = self.score(pcm)
        except Exception as exc:  # noqa: BLE001 — a model failure must not hang a call
            self._session = None
            self._unavailable = f"smart-turn inference failed: {exc}"
            logger.warning(
                "turn-taking: %s; falling back to energy endpointing", self._unavailable
            )
            return True
        return score >= self.completion_threshold

    # -- model I/O -----------------------------------------------------------

    def _model_input(self, pcm: bytes) -> Any:
        """Line audio -> exactly the tensor this model asked for.

        The shape is read off the session rather than hard-coded, because two
        generations of smart-turn take different things:

        * v1/v2 (Wav2Vec2) take raw samples, ``(1, N)`` at 16 kHz;
        * v3 (Whisper encoder) takes an 80-bin log-mel spectrogram,
          ``(1, 80, frames)`` — 800 frames for its 8 s window.

        Passing raw samples to a mel model does not fail; it scores noise. So
        the rank decides, and a rank nothing here can build is refused in
        ``_ensure`` rather than fed.
        """
        import numpy as np

        wide = resample(pcm, self.sample_rate, self.model_sample_rate)
        samples = np.frombuffer(wide, dtype="<i2").astype(np.float32) / 32768.0

        shape = self._input_shape()
        if len(shape) == 3:
            n_mels = shape[-2] if isinstance(shape[-2], int) and shape[-2] > 0 else _N_MELS
            frames = shape[-1] if isinstance(shape[-1], int) and shape[-1] > 0 else None
            if frames is None:
                frames = int(self.model_sample_rate * self.window_ms / 1000.0) // _HOP
            samples = self._fit(samples, frames * _HOP)
            return log_mel(samples, n_mels=n_mels, n_frames=frames)[None, :, :]

        want = None
        tail = shape[-1] if shape else None
        if isinstance(tail, int) and tail > 0:
            want = tail
        if want is None:
            want = int(self.model_sample_rate * self.window_ms / 1000.0)
        return self._fit(samples, want).reshape(1, -1)

    def _input_shape(self) -> list:
        with suppress(Exception):
            return list(self._session.get_inputs()[0].shape)
        return []

    @staticmethod
    def _fit(samples: Any, want: int) -> Any:
        import numpy as np

        if samples.size >= want:
            return samples[-want:]  # the *end* of the turn is what is judged
        return np.pad(samples, (want - samples.size, 0))

    @staticmethod
    def _probability(raw: Any) -> float:
        """Whatever the model emitted -> P(the speaker finished).

        Two heads are in the wild: a 2-logit classifier and a single sigmoid
        output. Telling them apart by shape is more durable than assuming.
        """
        import numpy as np

        arr = np.asarray(raw, dtype=np.float64).reshape(-1)
        if arr.size == 0:
            raise ValueError("model returned nothing")
        if arr.size >= 2:
            # Softmax over the last two logits: index 1 is "complete".
            pair = arr[-2:]
            pair = pair - pair.max()
            exp = np.exp(pair)
            return float(exp[1] / exp.sum())
        value = float(arr[0])
        if 0.0 <= value <= 1.0:
            return value
        return float(1.0 / (1.0 + np.exp(-value)))

    def score(self, pcm: bytes) -> float:
        """Probability the speaker finished. Overridden in tests with a stub."""
        self._ensure()
        if self._session is None:
            raise RuntimeError(self._unavailable or "smart-turn model not loaded")
        tensor = self._model_input(pcm)
        name = self._session.get_inputs()[0].name
        outputs = self._session.run(None, {name: tensor})
        if not outputs:
            raise ValueError("model returned no outputs")
        return self._probability(outputs[0])


# -- Whisper-style log-mel, in numpy ------------------------------------------
#
# smart-turn v3 sits on a Whisper encoder and is fed what Whisper is fed: 80
# mel bins from a 400-point STFT hopped every 160 samples (25 ms / 10 ms at
# 16 kHz), log10, clamped to 8 dB below the loudest bin, then mapped into
# roughly [-1, 1]. That is ~40 lines, and pulling in torch or librosa for it on
# a phone line would be absurd.

_N_FFT = 400
_HOP = 160
_N_MELS = 80
_mel_filters: dict[tuple[int, int, int], Any] = {}


def _hz_to_mel(hz: Any) -> Any:
    """Slaney scale: linear to 1 kHz, logarithmic above (what Whisper uses)."""
    import numpy as np

    hz = np.asarray(hz, dtype=np.float64)
    mel = hz / (200.0 / 3.0)
    high = hz >= 1000.0
    logstep = np.log(6.4) / 27.0
    return np.where(high, 15.0 + np.log(np.maximum(hz, 1e-9) / 1000.0) / logstep, mel)


def _mel_to_hz(mel: Any) -> Any:
    import numpy as np

    mel = np.asarray(mel, dtype=np.float64)
    hz = mel * (200.0 / 3.0)
    high = mel >= 15.0
    logstep = np.log(6.4) / 27.0
    return np.where(high, 1000.0 * np.exp(logstep * (mel - 15.0)), hz)


def mel_filterbank(sample_rate: int = 16000, n_fft: int = _N_FFT, n_mels: int = _N_MELS) -> Any:
    """Slaney-normalised triangular filters, ``(n_mels, n_fft // 2 + 1)``."""
    import numpy as np

    key = (sample_rate, n_fft, n_mels)
    cached = _mel_filters.get(key)
    if cached is not None:
        return cached
    fft_freqs = np.linspace(0.0, sample_rate / 2.0, n_fft // 2 + 1)
    mel_pts = np.linspace(_hz_to_mel(0.0), _hz_to_mel(sample_rate / 2.0), n_mels + 2)
    hz_pts = _mel_to_hz(mel_pts)
    weights = np.zeros((n_mels, len(fft_freqs)), dtype=np.float64)
    for i in range(n_mels):
        lo, mid, hi = hz_pts[i], hz_pts[i + 1], hz_pts[i + 2]
        up = (fft_freqs - lo) / max(mid - lo, 1e-9)
        down = (hi - fft_freqs) / max(hi - mid, 1e-9)
        weights[i] = np.maximum(0.0, np.minimum(up, down))
        weights[i] *= 2.0 / (hi - lo)  # Slaney: equal area per band
    out = weights.astype(np.float32)
    _mel_filters[key] = out
    return out


def log_mel(
    samples: Any, *, n_mels: int = _N_MELS, n_frames: int | None = None, sample_rate: int = 16000
) -> Any:
    """float32 16 kHz samples -> ``(n_mels, frames)`` Whisper-style features."""
    import numpy as np

    x = np.asarray(samples, dtype=np.float32).reshape(-1)
    pad = _N_FFT // 2
    if x.size <= pad:
        x = np.pad(x, (0, pad + 1 - x.size))
    x = np.pad(x, (pad, pad), mode="reflect")
    n = 1 + (x.size - _N_FFT) // _HOP
    idx = np.arange(_N_FFT)[None, :] + _HOP * np.arange(n)[:, None]
    window = np.hanning(_N_FFT + 1)[:-1].astype(np.float32)  # periodic Hann
    spec = np.fft.rfft(x[idx] * window, axis=1)
    power = (spec.real**2 + spec.imag**2).T  # (bins, frames)
    power = power[:, :-1]  # Whisper drops the final frame
    mel = mel_filterbank(sample_rate, _N_FFT, n_mels) @ power
    log_spec = np.log10(np.maximum(mel, 1e-10))
    log_spec = np.maximum(log_spec, log_spec.max() - 8.0)
    log_spec = (log_spec + 4.0) / 4.0
    if n_frames is not None:
        if log_spec.shape[1] >= n_frames:
            log_spec = log_spec[:, -n_frames:]
        else:
            log_spec = np.pad(log_spec, ((0, 0), (n_frames - log_spec.shape[1], 0)))
    return log_spec.astype(np.float32)


#: Where a fetched smart-turn model lands, following the same
#: ``REMEDY_HOME/<area>/models/`` shape the vision runtime uses.
SMART_TURN_DIRNAME = "smart-turn"


def smart_turn_model_path(home: Path | str | None = None) -> str:
    """The pinned smart-turn model on this machine, or "" if none is here yet.

    Nothing telephony-related ships with Remedy, so the model arrives only when
    the owner asks for it (``telephony.consent.COMPONENTS``). Looking for it
    here is what lets ``make_detector`` pick it up without every caller having
    to know the path — which is why the semantic detector was unreachable:
    ``make_detector`` needed a path and nothing ever passed one.
    """
    import os

    base = Path(
        home or os.environ.get("REMEDY_HOME") or "~/.remedy"
    ).expanduser()
    root = base / "voice" / "models" / SMART_TURN_DIRNAME
    if not root.is_dir():
        return ""
    # A pinned release is one .onnx file; take the newest if a re-pin left two.
    found = sorted(
        (p for p in root.glob("*.onnx") if p.is_file()),
        key=lambda p: p.stat().st_mtime,
        reverse=True,
    )
    return str(found[0]) if found else ""


def make_detector(
    model_path: str = "", *, home: Path | str | None = None
) -> TurnDetector:
    """Best detector available, without ever leaving the caller without one.

    With no explicit path, look for a model the owner has already fetched. An
    absent model is the normal case and is not a failure: energy endpointing is
    the floor, and it is what every call used before the model existed.
    """
    path = model_path or smart_turn_model_path(home)
    if path:
        smart = SmartTurnDetector(model_path=path)
        if smart.available:
            logger.info("turn-taking: semantic endpointing via %s", path)
            return smart
        logger.info("turn-taking: %s; using energy endpointing", smart.unavailable_reason)
    return EnergyTurnDetector()
