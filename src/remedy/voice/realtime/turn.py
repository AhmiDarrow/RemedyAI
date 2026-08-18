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
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Protocol

from remedy.telephony.narrowband import FRAME_MS, rms

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
    #: Never wait longer than this regardless of the model.
    max_wait_ms: float = 2400.0
    energy: EnergyTurnDetector = field(default_factory=EnergyTurnDetector)

    _session: Any = field(default=None, init=False)
    _tried: bool = field(default=False, init=False)
    _unavailable: str = field(default="", init=False)
    _buffer: bytearray = field(default_factory=bytearray, init=False)
    _speech_ms: float = field(default=0.0, init=False)

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
            import onnxruntime  # type: ignore
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
        self._speech_ms = 0.0

    def feed(self, pcm: bytes, at: float) -> TurnEvent | None:
        event = self.energy.feed(pcm, at)
        if self.energy.speaking or event is TurnEvent.ENDPOINT:
            self._buffer.extend(pcm)
            self._speech_ms += self.energy.frame_ms
        if event is TurnEvent.SPEECH_START:
            self._buffer.clear()
            self._buffer.extend(pcm)
            self._speech_ms = self.energy.frame_ms
            return event
        if event is not TurnEvent.ENDPOINT:
            return None
        # Energy says the turn ended. Ask the model whether the thought did.
        if self._speech_ms >= self.max_wait_ms or not self.available:
            self._buffer.clear()
            self._speech_ms = 0.0
            return TurnEvent.ENDPOINT
        if self._complete(bytes(self._buffer)):
            self._buffer.clear()
            self._speech_ms = 0.0
            return TurnEvent.ENDPOINT
        # Mid-sentence pause: keep listening.
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

    def score(self, pcm: bytes) -> float:
        """Probability the speaker finished. Overridden in tests with a stub."""
        raise NotImplementedError("smart-turn ONNX I/O wiring lands with the model pin")


def make_detector(model_path: str = "") -> TurnDetector:
    """Best detector available, without ever leaving the caller without one."""
    if model_path:
        smart = SmartTurnDetector(model_path=model_path)
        if smart.available:
            return smart
        logger.info("turn-taking: %s; using energy endpointing", smart.unavailable_reason)
    return EnergyTurnDetector()
