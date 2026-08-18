"""Realtime duplex voice — the loop that has to sound human on a phone line."""

from remedy.voice.realtime.metrics import BAR, CallMetrics, HumanBar
from remedy.voice.realtime.pipeline import (
    PipelineConfig,
    PipelineState,
    VoicePipeline,
)
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
    "PipelineConfig",
    "PipelineState",
    "SmartTurnDetector",
    "TurnEvent",
    "VoicePipeline",
    "make_detector",
]
