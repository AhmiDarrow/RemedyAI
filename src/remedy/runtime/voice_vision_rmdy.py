"""RMDY voice/vision handlers for the supervised tool worker.

Go ``httpapi.VoiceWorker`` / ``VisionWorker`` call these over KindToolRequest
frames. ML engines stay in the managed voice runtime (via ``remedy.voice.service``);
vision install/start/stop stay in-process on this worker so progress state matches.
"""

from __future__ import annotations

import base64
import os
import tempfile
from collections.abc import Mapping
from pathlib import Path
from typing import Any


def _home(inp: Mapping[str, Any]) -> str | None:
    raw = inp.get("home_dir")
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw)


def _cfg(inp: Mapping[str, Any]) -> dict[str, Any]:
    """Build a config dict with home_dir; merge on-disk settings when available."""
    home = _home(inp)
    cfg: dict[str, Any] = {}
    if home:
        cfg["home_dir"] = home
        os.environ.setdefault("REMEDY_HOME", home)
    try:
        from remedy.interfaces.api_support import load_config

        loaded = load_config()
        if isinstance(loaded, dict):
            merged = dict(loaded)
            merged.update(cfg)
            return merged
    except Exception:  # noqa: BLE001 — worker must answer without full settings
        pass
    return cfg


def voice_speak(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.voice.service import synthesize

    text = str(inp.get("text") or "")
    gender = str(inp.get("gender") or "female")
    voice = inp.get("voice")
    speed = inp.get("speed")
    out = synthesize(
        text,
        gender=gender,
        voice=str(voice) if voice else None,
        speed=float(speed) if speed is not None else None,
        home_dir=_home(inp),
    )
    if out is None:
        return {"unavailable": True, "wav_b64": "", "sample_rate": 0}
    wav, sr = out
    return {
        "unavailable": False,
        "wav_b64": base64.b64encode(wav).decode("ascii"),
        "sample_rate": int(sr),
    }


def voice_transcribe(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.voice.service import transcribe_file

    b64 = str(inp.get("audio_b64") or "")
    if not b64:
        raise ValueError("audio_b64 is required")
    raw = base64.b64decode(b64, validate=False)
    suffix = str(inp.get("suffix") or ".webm")
    if not suffix.startswith("."):
        suffix = "." + suffix
    language = inp.get("language")
    with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as tmp:
        tmp.write(raw)
        path = Path(tmp.name)
    try:
        result = transcribe_file(
            path,
            language=str(language) if language else None,
            home_dir=_home(inp),
        )
    finally:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
    if not result:
        return {"unavailable": True, "text": "", "language": ""}
    return {
        "unavailable": False,
        "text": str(result.get("text") or ""),
        "language": str(result.get("language") or ""),
    }


def voice_install(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    """Mirror FastAPI ``/api/voice/install`` component routing."""
    home = _home(inp)
    comp = str(inp.get("component") or "tts").strip().lower()
    if comp in ("all", "*", "pack", "voice"):
        from remedy.voice.service import install_voice_pack_background

        started = bool(install_voice_pack_background(home))
        return {"ok": True, "started": started}
    if comp == "tts":
        from remedy.voice.service import (
            install_tts_background,
            install_voice_pack_background,
            tts_deps_available,
        )

        if not tts_deps_available():
            started = bool(install_voice_pack_background(home))
            return {"ok": True, "started": started}
        started = bool(install_tts_background(home))
        return {"ok": True, "started": started}
    if comp == "stt":
        from remedy.voice.service import (
            install_stt_background,
            install_voice_pack_background,
            stt_deps_available,
        )

        if not stt_deps_available():
            started = bool(install_voice_pack_background(home))
            return {"ok": True, "started": started}
        started = bool(install_stt_background(home))
        return {"ok": True, "started": started}
    if comp in ("smart-turn", "smart_turn"):
        from remedy.voice.service import (
            install_smart_turn_background,
            install_voice_pack_background,
            smart_turn_deps_available,
        )

        if not smart_turn_deps_available():
            started = bool(install_voice_pack_background(home))
            return {"ok": True, "started": started}
        started = bool(install_smart_turn_background(home))
        return {"ok": True, "started": started}
    if comp in ("chatterbox", "hq"):
        from remedy.voice.chatterbox import install_chatterbox_background

        started = bool(install_chatterbox_background(home))
        return {"ok": True, "started": started}
    return {"ok": False, "started": False, "error": f"Unknown voice piece {comp!r}."}


def vision_activate(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.vision.service import activate_bundle

    enabled = bool(inp.get("enabled", True))
    return activate_bundle(cfg=_cfg(inp), enabled=enabled)


def vision_install(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.vision.service import start_install

    return start_install(
        cfg=_cfg(inp),
        model_id=str(inp["model_id"]) if inp.get("model_id") else None,
        runtime_id=str(inp["runtime_id"]) if inp.get("runtime_id") else None,
        prefer_cuda=bool(inp.get("prefer_cuda", False)),
    )


def vision_cancel_install(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.vision.service import cancel_install

    _ = inp
    return cancel_install()


def vision_reinstall_runtime(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.vision.service import reinstall_runtime

    prefer = inp.get("prefer_cuda")
    return reinstall_runtime(
        cfg=_cfg(inp),
        prefer_cuda=True if prefer is None else bool(prefer),
    )


def vision_uninstall(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.vision.service import uninstall

    return uninstall(cfg=_cfg(inp), keep_models=bool(inp.get("keep_models", False)))


def vision_start(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.vision.service import ensure_server

    return ensure_server(_cfg(inp))


def vision_stop(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.vision.service import stop

    return stop(_cfg(inp))


def vision_progress(inp: Mapping[str, Any]) -> Mapping[str, Any]:
    from remedy.vision import progress as prog

    _ = inp
    snap = prog.snapshot()
    return snap if isinstance(snap, dict) else {}
