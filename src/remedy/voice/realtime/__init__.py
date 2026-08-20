"""Realtime duplex voice — the loop that has to sound human on a phone line."""

from remedy.voice.realtime.metrics import BAR, CallMetrics, HumanBar
from remedy.voice.realtime.pipeline import (
    PipelineConfig,
    PipelineState,
    VoicePipeline,
)
from remedy.voice.realtime.stt_stream import WhisperStream
from remedy.voice.realtime.tts import LocalTts
from remedy.voice.realtime.tts_stream import iter_frames
from remedy.voice.realtime.turn import (
    EnergyTurnDetector,
    SmartTurnDetector,
    TurnEvent,
    make_detector,
)

__all__ = [
    "BAR",
    "CallMetrics",
    "EnergyTurnDetector",
    "HumanBar",
    "LocalTts",
    "iter_frames",
    "PipelineConfig",
    "PipelineState",
    "SmartTurnDetector",
    "TurnEvent",
    "VoicePipeline",
    "WhisperStream",
    "make_detector",
]
