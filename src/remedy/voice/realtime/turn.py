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

    _session: Any = field(default=None, init=False)
    _tried: bool = field(default=False, init=False)
    _unavailable: str = field(default="", init=False)
    _buffer: bytearray = field(default_factory=bytearray, init=False)
    _held_ms: float = field(default=0.0, init=False)
    _holding: bool = field(default=False, init=False)

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

    def reset(self) -> None:
        self.energy.reset()
        self._buffer.clear()
        self._release()

    def _release(self) -> None:
        """Stop counting against ``max_wait_ms``; the model is not holding us."""
        self._held_ms = 0.0
        self._holding = False

    def _endpoint(self) -> TurnEvent:
        self._buffer.clear()
        self._release()
        return TurnEvent.ENDPOINT

    @property
    def _window_bytes(self) -> int:
        return max(1, int(self.window_ms * self.sample_rate / 1000.0)) * 2

    def feed(self, pcm: bytes, at: float) -> TurnEvent | None:
        event = self.energy.feed(pcm, at)
        if self.energy.speaking or event is TurnEvent.ENDPOINT:
            self._buffer.extend(pcm)
            if len(self._buffer) > self._window_bytes:
                del self._buffer[: len(self._buffer) - self._window_bytes]
        if self._holding:
            # Only the time we have spent overruling energy counts here.
            self._held_ms += self.energy.frame_ms
        if event is TurnEvent.SPEECH_START:
            self._buffer.clear()
            self._buffer.extend(pcm)
            self._release()
            return event
        if event is not TurnEvent.ENDPOINT:
            return None
        # Energy says the turn ended. Ask the model whether the thought did.
        if self._held_ms >= self.max_wait_ms or not self.available:
            return self._endpoint()
        if self._complete(bytes(self._buffer)):
            return self._endpoint()
        # Mid-sentence pause: keep listening, and start the clock on how long we
        # are willing to be held there.
        self._holding = True
        self.energy._speaking = True  # noqa: SLF001 — same conceptual object
        self.energy._silent_ms = 0.0  # noqa: SLF001
        return None

    def _complete(self, pcm: bytes) -> bool:
        try:
            score = self.score(pcm)
        except Exception:  # noqa: BLE001 — a model wobble must not hang a call
            logger.debug("smart-turn inference failed; falling back to energy")
            return True
        return score >= self.completion_threshold

    # -- model I/O -----------------------------------------------------------

    def _model_input(self, pcm: bytes) -> Any:
        """Line audio -> exactly the tensor this model asked for.

        Shapes are read off the session rather than hard-coded: smart-turn has
        shipped as v2 and v3 with different fixed windows, and a model pinned
        later must not need a code change here.
        """
        import numpy as np

        wide = resample(pcm, self.sample_rate, self.model_sample_rate)
        samples = np.frombuffer(wide, dtype="<i2").astype(np.float32) / 32768.0

        want = None
        with suppress(Exception):
            shape = self._session.get_inputs()[0].shape
            tail = shape[-1]
            if isinstance(tail, int) and tail > 0:
                want = tail
        if want is None:
            want = int(self.model_sample_rate * self.window_ms / 1000.0)

        if samples.size >= want:
            samples = samples[-want:]  # the *end* of the turn is what is judged
        else:
            samples = np.pad(samples, (want - samples.size, 0))
        return samples.reshape(1, -1)

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
