"""REST API for local voice: speak-back (Kokoro TTS) + hearing (whisper STT).

All processing is on-device; audio never leaves this machine. When engines
are missing the endpoints answer 503 with ``fallback`` hints so the desktop
degrades to OS voices (speechSynthesis) instead of erroring at the owner.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import tempfile
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request, Response
from pydantic import BaseModel

from remedy.interfaces.api_support import load_config

logger = logging.getLogger(__name__)

# Mic clips are short dictation, not podcasts.
_MAX_AUDIO_BYTES = 25 * 1024 * 1024


class SpeakRequest(BaseModel):
    text: str
    voice: str | None = None
    speed: float | None = None


class VoiceSettingsPatch(BaseModel):
    tts_enabled: bool | None = None
    stt_enabled: bool | None = None
    speak_replies: bool | None = None
    voice_override: str | None = None
    speed: float | None = None
    stt_model: str | None = None
    language: str | None = None


class VoiceInstallRequest(BaseModel):
    component: str = "tts"  # tts | stt | smart-turn


def _home(cfg: dict[str, Any] | None) -> str | None:
    if isinstance(cfg, dict) and cfg.get("home_dir"):
        return str(cfg["home_dir"])
    return None


def _agent_gender(cfg: dict[str, Any] | None) -> str:
    if isinstance(cfg, dict):
        return str(cfg.get("agent_gender") or "female")
    return "female"


def register_voice_routes(
    app: FastAPI, *, runtime=None, gateway=None, memory=None
) -> None:
    """Register /api/voice/* routes."""
    _ = (runtime, gateway, memory)

    @app.get("/api/voice/status")
    async def voice_status_route() -> dict[str, Any]:
        from remedy.voice.service import voice_status

        cfg = load_config()
        return await asyncio.to_thread(
            voice_status, _home(cfg), agent_gender=_agent_gender(cfg)
        )

    @app.post("/api/voice/settings")
    async def voice_settings_route(patch: VoiceSettingsPatch) -> dict[str, Any]:
        from remedy.voice.service import load_voice_settings, save_voice_settings

        cfg = load_config()
        data = {k: v for k, v in patch.model_dump().items() if v is not None}
        if data:
            return await asyncio.to_thread(save_voice_settings, data, _home(cfg))
        return load_voice_settings(_home(cfg))

    @app.post("/api/voice/install")
    async def voice_install_route(req: VoiceInstallRequest) -> dict[str, Any]:
        cfg = load_config()
        comp = (req.component or "tts").strip().lower()
        if comp == "tts":
            from remedy.voice.service import install_tts_background, tts_deps_available

            if not tts_deps_available():
                return {
                    "ok": False,
                    "error": "voice extra not installed — pip install remedy-ai[voice]",
                }
            started = install_tts_background(_home(cfg))
            return {"ok": True, "started": started}
        if comp == "stt":
            # faster-whisper downloads on first model load; warm it in background.
            from remedy.voice.service import get_stt_model, stt_deps_available

            if not stt_deps_available():
                return {
                    "ok": False,
                    "error": "voice extra not installed — pip install remedy-ai[voice]",
                }
            import threading

            threading.Thread(
                target=get_stt_model, args=(_home(cfg),), daemon=True
            ).start()
            return {"ok": True, "started": True}
        if comp in ("smart-turn", "smart_turn"):
            from remedy.voice.service import (
                install_smart_turn_background,
                smart_turn_deps_available,
            )

            if not smart_turn_deps_available():
                return {
                    "ok": False,
                    "error": "onnxruntime not installed — pip install remedy-ai[voice]",
                }
            started = install_smart_turn_background(_home(cfg))
            return {"ok": True, "started": started}
        return {"ok": False, "error": f"unknown component {comp!r}"}

    @app.post("/api/voice/speak")
    async def voice_speak_route(req: SpeakRequest) -> Response:
        from remedy.voice.service import load_voice_settings, synthesize

        cfg = load_config()
        home = _home(cfg)
        settings = load_voice_settings(home)
        if not settings.get("tts_enabled", True):
            return Response(
                content='{"error":"tts disabled","fallback":"browser"}',
                status_code=503,
                media_type="application/json",
            )
        out = await asyncio.to_thread(
            synthesize,
            req.text or "",
            gender=_agent_gender(cfg),
            voice=req.voice,
            speed=req.speed,
            home_dir=home,
        )
        if out is None:
            return Response(
                content='{"error":"local TTS unavailable","fallback":"browser"}',
                status_code=503,
                media_type="application/json",
            )
        wav, sr = out
        return Response(
            content=wav,
            media_type="audio/wav",
            headers={"X-Sample-Rate": str(sr), "Cache-Control": "no-store"},
        )

    @app.post("/api/voice/transcribe")
    async def voice_transcribe_route(request: Request) -> Any:
        """Raw audio body (webm/ogg/wav/mp3) → {text}. Local whisper only."""
        from remedy.voice.service import load_voice_settings, transcribe_file

        cfg = load_config()
        home = _home(cfg)
        if not load_voice_settings(home).get("stt_enabled", True):
            return Response(
                content='{"error":"stt disabled"}',
                status_code=503,
                media_type="application/json",
            )
        body = await request.body()
        if not body:
            return Response(
                content='{"error":"empty audio"}',
                status_code=400,
                media_type="application/json",
            )
        if len(body) > _MAX_AUDIO_BYTES:
            return Response(
                content='{"error":"audio too large"}',
                status_code=413,
                media_type="application/json",
            )
        ctype = (request.headers.get("content-type") or "").lower()
        suffix = (
            ".webm"
            if "webm" in ctype
            else ".ogg"
            if "ogg" in ctype
            else ".mp3"
            if "mp3" in ctype or "mpeg" in ctype
            else ".wav"
        )
        lang = (request.query_params.get("language") or "").strip() or None
        tmp: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=suffix, delete=False
            ) as f:
                f.write(body)
                tmp = Path(f.name)
            result = await asyncio.to_thread(
                transcribe_file, tmp, language=lang, home_dir=home
            )
        finally:
            if tmp is not None:
                with contextlib.suppress(OSError):
                    tmp.unlink(missing_ok=True)
        if result is None:
            return Response(
                content=(
                    '{"error":"local STT unavailable — install the voice extra '
                    'and let the model download","fallback":"none"}'
                ),
                status_code=503,
                media_type="application/json",
            )
        return result
