"""Remedy Voice — local speech in and out (docs/LIFE_TASK_PARTNER.md §4).

Speak-back (TTS) and hearing (STT) are optional, local-first, and
license-clean for Remedy's commercial terms:

* **TTS — Kokoro-82M** (model Apache-2.0) via ``kokoro-onnx`` (MIT):
  near-SOTA quality per byte, CPU-realtime, with distinct female / male /
  neutral voices mapped from the owner's existing ``agent_gender`` setting.
* **STT — faster-whisper** (MIT, CTranslate2): Whisper quality on CPU;
  model size configurable (``small`` default, up to ``large-v3-turbo``).

Ships as the ``remedy-ai[voice]`` extra — nothing heavy lands in the base
install. Engines load lazily; when absent, ``/voice/status`` says so and the
desktop falls back to the OS voices (browser ``speechSynthesis``) so
speak-back still works with zero downloads. Audio never leaves this machine.
"""

from remedy.voice.service import (
    speakable_text,
    voice_for_gender,
    voice_status,
)

__all__ = ["speakable_text", "voice_for_gender", "voice_status"]
