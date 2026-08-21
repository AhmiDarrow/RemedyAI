"""REST API for local voice: speak-back, hearing, and smart-turn install.

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


class VoiceClientLog(BaseModel):
    message: str = ""


class VoiceIdentityPatch(BaseModel):
    pace: float | None = None
    pitch_semitones: float | None = None
    warmth: float | None = None
    articulation: float | None = None


class VoiceIdentityRevert(BaseModel):
    steps: int = 1


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
    tts_quality: str | None = None


class VoiceInstallRequest(BaseModel):
    component: str = "tts"  # tts | stt | smart-turn | chatterbox | all | pack


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
            saved = await asyncio.to_thread(save_voice_settings, data, _home(cfg))
            if str(saved.get("tts_quality") or "") == "hq":
                from remedy.voice.chatterbox import install_chatterbox_background

                await asyncio.to_thread(install_chatterbox_background, _home(cfg))
            return saved
        return load_voice_settings(_home(cfg))

    @app.post("/api/voice/install")
    async def voice_install_route(req: VoiceInstallRequest) -> dict[str, Any]:
        cfg = load_config()
        comp = (req.component or "tts").strip().lower()
        if comp in ("all", "*", "pack", "voice"):
            from remedy.voice.service import install_voice_pack_background

            started = await asyncio.to_thread(install_voice_pack_background, _home(cfg))
            return {"ok": True, "started": started}
        if comp == "tts":
            from remedy.voice.service import (
                install_tts_background,
                install_voice_pack_background,
                tts_deps_available,
            )

            if not tts_deps_available():
                started = await asyncio.to_thread(install_voice_pack_background, _home(cfg))
                return {"ok": True, "started": started}
            started = await asyncio.to_thread(install_tts_background, _home(cfg))
            return {"ok": True, "started": started}
        if comp == "stt":
            from remedy.voice.service import (
                install_stt_background,
                install_voice_pack_background,
                stt_deps_available,
            )

            if not stt_deps_available():
                started = await asyncio.to_thread(install_voice_pack_background, _home(cfg))
                return {"ok": True, "started": started}
            started = await asyncio.to_thread(install_stt_background, _home(cfg))
            return {"ok": True, "started": started}
        if comp in ("smart-turn", "smart_turn"):
            from remedy.voice.service import (
                install_smart_turn_background,
                install_voice_pack_background,
                smart_turn_deps_available,
            )

            # Same as tts/stt: without onnxruntime the model can never load,
            # so fetch the pack (which brings it) instead of a dead download.
            if not smart_turn_deps_available():
                started = await asyncio.to_thread(install_voice_pack_background, _home(cfg))
                return {"ok": True, "started": started}
            started = await asyncio.to_thread(install_smart_turn_background, _home(cfg))
            return {"ok": True, "started": started}
        if comp in ("chatterbox", "hq"):
            from remedy.voice.chatterbox import install_chatterbox_background

            started = await asyncio.to_thread(install_chatterbox_background, _home(cfg))
            return {"ok": True, "started": started}
        return {"ok": False, "error": f"Unknown voice piece {comp!r}."}

    @app.post("/api/voice/client-log")
    async def voice_client_log_route(req: VoiceClientLog) -> dict[str, Any]:
        """The desktop reports why it could not play her voice, so the
        answer is in remedy.log instead of a devtools console nobody opens."""
        logger.warning("voice client: %s", str(req.message or "")[:400])
        return {"ok": True}

    @app.get("/api/voice/identity")
    async def voice_identity_route() -> dict[str, Any]:
        from remedy.voice.identity import load

        return load(_home(load_config())).public()

    @app.post("/api/voice/identity/adjust")
    async def voice_identity_adjust_route(patch: VoiceIdentityPatch) -> dict[str, Any]:
        """Nudge the voice traits by small deltas (clamped, journaled)."""
        from remedy.voice.identity import evolve

        home = _home(load_config())
        ident = await asyncio.to_thread(
            evolve,
            home,
            pace=patch.pace,
            pitch_semitones=patch.pitch_semitones,
            warmth=patch.warmth,
            articulation=patch.articulation,
        )
        return ident.public()

    @app.post("/api/voice/identity/revert")
    async def voice_identity_revert_route(req: VoiceIdentityRevert) -> dict[str, Any]:
        from remedy.voice.identity import revert

        ident = await asyncio.to_thread(revert, _home(load_config()), steps=req.steps)
        return ident.public()

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
                    '{"error":"Hearing is not ready yet. Open Settings → Voice '
                    'to download it.","fallback":"none"}'
                ),
                status_code=503,
                media_type="application/json",
            )
        return result
