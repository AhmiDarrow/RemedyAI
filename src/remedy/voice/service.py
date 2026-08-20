"""Voice engines: Kokoro TTS + faster-whisper STT + smart-turn, lazy and optional.

Everything degrades gracefully: no [voice] extra installed → status reports
why, /voice/speak returns 503 with fallback="browser" so the desktop uses OS
voices, /voice/transcribe returns 503 and the mic button explains itself.
"""

from __future__ import annotations

import hashlib
import io
import json
import logging
import os
import struct
import threading
import time
import urllib.request
import wave
from dataclasses import dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Homes + settings (self-contained voice.json, like the vision stack)
# ---------------------------------------------------------------------------


def voice_home(home_dir: Path | str | None = None) -> Path:
    base = Path(
        home_dir or os.environ.get("REMEDY_HOME", "~/.remedy")
    ).expanduser()
    d = base / "voice"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _settings_path(home_dir: Path | str | None = None) -> Path:
    return voice_home(home_dir) / "voice.json"


_DEFAULT_SETTINGS: dict[str, Any] = {
    "tts_enabled": True,
    "stt_enabled": True,
    # Speak assistant replies out loud automatically (Grove toggle mirrors this)
    "speak_replies": False,
    # Explicit voice id wins over the agent_gender mapping when set
    "voice_override": "",
    "speed": 1.0,
    # faster-whisper model size: tiny | base | small | medium | large-v3-turbo
    "stt_model": "small",
    # None/"" = autodetect
    "language": "",
}


def load_voice_settings(home_dir: Path | str | None = None) -> dict[str, Any]:
    out = dict(_DEFAULT_SETTINGS)
    try:
        raw = json.loads(_settings_path(home_dir).read_text(encoding="utf-8"))
        if isinstance(raw, dict):
            for k in out:
                if k in raw:
                    out[k] = raw[k]
    except (OSError, json.JSONDecodeError):
        pass
    return out


def save_voice_settings(
    patch: dict[str, Any], home_dir: Path | str | None = None
) -> dict[str, Any]:
    cur = load_voice_settings(home_dir)
    for k, v in (patch or {}).items():
        if k in _DEFAULT_SETTINGS and v is not None:
            cur[k] = v
    try:
        cur["speed"] = min(2.0, max(0.5, float(cur.get("speed") or 1.0)))
    except (TypeError, ValueError):
        cur["speed"] = 1.0
    from remedy.core.atomic_json import write_text_atomic

    write_text_atomic(
        _settings_path(home_dir), json.dumps(cur, indent=2) + "\n"
    )
    return cur


# ---------------------------------------------------------------------------
# Gender → voice mapping (agent_gender is the owner's existing choice)
# ---------------------------------------------------------------------------

# Kokoro v1.0 voice ids: af_/bf_ = female (US/GB), am_/bm_ = male.
# "neutral" has no true androgynous Kokoro voice — we pick a low-warmth
# female voice by default; the owner can set voice_override to anything.
GENDER_VOICES: dict[str, str] = {
    "female": "af_heart",
    "male": "am_michael",
    "neutral": "af_sky",
}

KNOWN_VOICES: tuple[str, ...] = (
    "af_heart",
    "af_bella",
    "af_nicole",
    "af_sarah",
    "af_sky",
    "am_adam",
    "am_michael",
    "am_fenrir",
    "bf_emma",
    "bf_isabella",
    "bm_george",
    "bm_lewis",
)


def voice_for_gender(gender: str | None, override: str | None = None) -> str:
    """Resolve the speaking voice from agent_gender, honoring an override."""
    ov = (override or "").strip().lower()
    if ov:
        return ov
    try:
        from remedy.core.agent_identity import normalize_agent_gender

        g = normalize_agent_gender(gender)
    except Exception:
        g = (gender or "").strip().lower()
    return GENDER_VOICES.get(g, GENDER_VOICES["female"])


# ---------------------------------------------------------------------------
# Speakable text: strip markdown/tool residue before synthesis
# ---------------------------------------------------------------------------


def speakable_text(markdown: str, *, max_chars: int = 1400) -> str:
    """Plain, listenable text from a chat reply.

    Drops code blocks entirely (nobody wants JSON read aloud), unwraps
    links/emphasis, collapses whitespace, and truncates on a sentence edge.
    """
    import re

    t = markdown or ""
    # Truncate BEFORE regex work — output is capped anyway, and the link/image
    # patterns are O(n²) on adversarial bracket runs (ReDoS guard).
    hard_cap = max_chars * 8
    if len(t) > hard_cap:
        t = t[:hard_cap]
    t = re.sub(r"```.*?```", " (code shown on screen) ", t, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", " ", t)  # images
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", t)  # links → label
    t = re.sub(r"^#{1,6}\s+", "", t, flags=re.M)  # headings
    t = re.sub(r"^\s*[-*+]\s+", "", t, flags=re.M)  # bullets
    t = re.sub(r"^\s*\|.*\|\s*$", " ", t, flags=re.M)  # table rows
    t = re.sub(r"[*_~]{1,3}([^*_~]+)[*_~]{1,3}", r"\1", t)  # emphasis
    t = re.sub(r"https?://\S+", " ", t)  # bare urls
    t = re.sub(r"\s+", " ", t).strip()
    if len(t) > max_chars:
        cut = t[:max_chars]
        dot = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
        t = cut[: dot + 1] if dot > 200 else cut
    return t


# ---------------------------------------------------------------------------
# WAV encoding (stdlib only)
# ---------------------------------------------------------------------------


def encode_wav(samples: Any, sample_rate: int) -> bytes:
    """Float samples in [-1, 1] (numpy array or sequence) → 16-bit PCM WAV."""
    try:
        pcm = (
            (samples.clip(-1.0, 1.0) * 32767.0).astype("<i2").tobytes()  # numpy
        )
    except AttributeError:
        ints = [
            max(-32767, min(32767, int(float(s) * 32767.0))) for s in samples
        ]
        pcm = struct.pack(f"<{len(ints)}h", *ints)
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(int(sample_rate))
        w.writeframes(pcm)
    return buf.getvalue()


# ---------------------------------------------------------------------------
# TTS engine (Kokoro via kokoro-onnx) — lazy singleton
# ---------------------------------------------------------------------------

_TTS_MODEL_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    "kokoro-v1.0.onnx"
)
_TTS_VOICES_URL = (
    "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
    "voices-v1.0.bin"
)

_tts_lock = threading.Lock()
_tts_engine: Any | None = None
_install_state: dict[str, Any] = {"tts": None, "stt": None, "smart-turn": None}


def tts_paths(home_dir: Path | str | None = None) -> tuple[Path, Path]:
    d = voice_home(home_dir) / "tts"
    d.mkdir(parents=True, exist_ok=True)
    return d / "kokoro-v1.0.onnx", d / "voices-v1.0.bin"


def tts_deps_available() -> bool:
    try:
        import kokoro_onnx  # noqa: F401

        return True
    except Exception:
        return False


def tts_installed(home_dir: Path | str | None = None) -> bool:
    model, voices = tts_paths(home_dir)
    return model.is_file() and voices.is_file()


def _download(url: str, dest: Path, state_key: str) -> None:
    tmp = dest.with_suffix(dest.suffix + ".part")
    req = urllib.request.Request(url, headers={"User-Agent": "remedy-voice"})
    with urllib.request.urlopen(req, timeout=60) as resp:
        total = int(resp.headers.get("Content-Length") or 0)
        done = 0
        with tmp.open("wb") as f:
            while True:
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                f.write(chunk)
                done += len(chunk)
                if total:
                    st = _install_state.get(state_key)
                    if isinstance(st, dict):
                        st["percent"] = round(done * 100 / total, 1)
    tmp.replace(dest)


def install_tts(home_dir: Path | str | None = None) -> None:
    """Blocking download of Kokoro model files (~340 MB total)."""
    model, voices = tts_paths(home_dir)
    _install_state["tts"] = {"status": "downloading", "percent": 0.0}
    try:
        if not model.is_file():
            _download(_TTS_MODEL_URL, model, "tts")
        if not voices.is_file():
            _download(_TTS_VOICES_URL, voices, "tts")
        _install_state["tts"] = {"status": "done", "percent": 100.0}
    except Exception as exc:
        _install_state["tts"] = {"status": "error", "error": str(exc)[:300]}
        raise


def install_tts_background(home_dir: Path | str | None = None) -> bool:
    # Lock the check-and-mark so two rapid /voice/install calls can't both
    # spawn a downloader racing on the same .part file (corrupt model).
    with _tts_lock:
        st = _install_state.get("tts")
        if isinstance(st, dict) and st.get("status") == "downloading":
            return False
        _install_state["tts"] = {"status": "downloading", "percent": 0.0}
    t = threading.Thread(target=install_tts, args=(home_dir,), daemon=True)
    t.start()
    return True


def get_tts_engine(home_dir: Path | str | None = None) -> Any | None:
    """Lazy Kokoro engine, or None with a logged reason."""
    global _tts_engine
    with _tts_lock:
        if _tts_engine is not None:
            return _tts_engine
        if not tts_deps_available() or not tts_installed(home_dir):
            return None
        try:
            from kokoro_onnx import Kokoro

            model, voices = tts_paths(home_dir)
            _tts_engine = Kokoro(str(model), str(voices))
            return _tts_engine
        except Exception as exc:
            logger.warning("voice: Kokoro init failed: %s", exc)
            return None


def synthesize(
    text: str,
    *,
    gender: str | None = None,
    voice: str | None = None,
    speed: float | None = None,
    home_dir: Path | str | None = None,
) -> tuple[bytes, int] | None:
    """Text → (wav_bytes, sample_rate); None when the engine is unavailable."""
    engine = get_tts_engine(home_dir)
    if engine is None:
        return None
    cfg = load_voice_settings(home_dir)
    v = voice_for_gender(gender, voice or cfg.get("voice_override"))
    spd = float(speed or cfg.get("speed") or 1.0)
    clean = speakable_text(text)
    if not clean:
        return None
    samples, sr = engine.create(clean, voice=v, speed=spd)
    return encode_wav(samples, sr), int(sr)


# ---------------------------------------------------------------------------
# STT engine (faster-whisper) — lazy singleton
# ---------------------------------------------------------------------------

_stt_lock = threading.Lock()
_stt_model: Any | None = None
_stt_model_size: str | None = None

_VALID_STT = ("tiny", "base", "small", "medium", "large-v3", "large-v3-turbo")


def stt_deps_available() -> bool:
    try:
        import faster_whisper  # noqa: F401

        return True
    except Exception:
        return False


def stt_cache_dir(home_dir: Path | str | None = None) -> Path:
    d = voice_home(home_dir) / "stt"
    d.mkdir(parents=True, exist_ok=True)
    return d


def stt_installed(home_dir: Path | str | None = None) -> bool:
    """True when at least one whisper model snapshot exists locally."""
    d = stt_cache_dir(home_dir)
    try:
        return any(p.is_dir() and any(p.iterdir()) for p in d.iterdir())
    except OSError:
        return False


def get_stt_model(home_dir: Path | str | None = None) -> Any | None:
    """Lazy WhisperModel (downloads on first use into ~/.remedy/voice/stt)."""
    global _stt_model, _stt_model_size
    if not stt_deps_available():
        return None
    size = str(load_voice_settings(home_dir).get("stt_model") or "small")
    if size not in _VALID_STT:
        size = "small"
    with _stt_lock:
        if _stt_model is not None and _stt_model_size == size:
            return _stt_model
        try:
            from faster_whisper import WhisperModel

            _install_state["stt"] = {"status": "loading", "model": size}
            _stt_model = WhisperModel(
                size,
                device="cpu",
                compute_type="int8",
                download_root=str(stt_cache_dir(home_dir)),
            )
            _stt_model_size = size
            _install_state["stt"] = {"status": "done", "model": size}
            return _stt_model
        except Exception as exc:
            logger.warning("voice: whisper init failed: %s", exc)
            _install_state["stt"] = {"status": "error", "error": str(exc)[:300]}
            return None


def transcribe_file(
    path: Path | str,
    *,
    language: str | None = None,
    home_dir: Path | str | None = None,
) -> dict[str, Any] | None:
    """Audio file (wav/webm/ogg/mp3 — PyAV decodes) → {text, language, duration}."""
    model = get_stt_model(home_dir)
    if model is None:
        return None
    cfg = load_voice_settings(home_dir)
    lang = (language or cfg.get("language") or "").strip() or None
    start = time.time()
    segments, info = model.transcribe(
        str(path),
        language=lang,
        vad_filter=True,
        beam_size=5,
    )
    text = " ".join(s.text.strip() for s in segments).strip()
    return {
        "text": text,
        "language": getattr(info, "language", lang) or "",
        "duration": round(getattr(info, "duration", 0.0) or 0.0, 2),
        "took_s": round(time.time() - start, 2),
    }


# ---------------------------------------------------------------------------
# smart-turn (semantic endpointing for live calls) — a pinned ONNX file
# ---------------------------------------------------------------------------
#
# ``telephony.consent.COMPONENTS["smart-turn"]`` declares the component and
# its licence; this is the fetch that declaration promised. The detector in
# ``voice.realtime.turn`` looks in ``voice/models/smart-turn/*.onnx`` on every
# ``make_detector`` call, so a file landing here is picked up by the next call
# without a restart.


@dataclass(frozen=True, slots=True)
class SmartTurnPin:
    """One exact file from the Hub: repo + commit + name + size + digest."""

    repo: str
    revision: str
    filename: str
    size: int
    sha256: str
    licence: str

    @property
    def url(self) -> str:
        from remedy.runtime.rmb.hf import HF_API, sanitize_repo, sanitize_revision

        return (
            f"{HF_API}/{sanitize_repo(self.repo)}/resolve/"
            f"{sanitize_revision(self.revision)}/{self.filename}"
        )


#: smart-turn v3.2, CPU build (int8, Whisper-tiny encoder, 8 s log-mel window).
#: Pinned to a commit rather than ``main`` so the digest below stays true.
SMART_TURN_PIN = SmartTurnPin(
    repo="pipecat-ai/smart-turn-v3",
    revision="f766f81d3cfdf7737ac64aad813d91bbfd56bf93",
    filename="smart-turn-v3.2-cpu.onnx",
    size=8_679_182,
    sha256="2bb026316b14a660486a75b1733cd3fbab8c2fd0314dc9af7be49f8cca967e4f",
    licence="BSD-2-Clause",
)

_smart_turn_lock = threading.Lock()


def smart_turn_dir(home_dir: Path | str | None = None) -> Path:
    from remedy.voice.realtime.turn import SMART_TURN_DIRNAME

    return voice_home(home_dir) / "models" / SMART_TURN_DIRNAME


def smart_turn_path(home_dir: Path | str | None = None) -> Path:
    return smart_turn_dir(home_dir) / SMART_TURN_PIN.filename


def smart_turn_deps_available() -> bool:
    try:
        import onnxruntime  # noqa: F401

        return True
    except Exception:
        return False


def smart_turn_installed(home_dir: Path | str | None = None) -> bool:
    return smart_turn_path(home_dir).is_file()


def _hf_open(url: str, *, timeout: float = 120.0) -> Any:
    """Open a Hub URL through the RMB opener (redirects pinned to HF hosts).

    Tests replace this to fake the HTTP layer; nothing else here touches it.
    """
    from remedy.runtime.rmb.hf import _auth_headers, _urlopen

    req = urllib.request.Request(url, headers=_auth_headers())
    return _urlopen(req, timeout=timeout)


def install_smart_turn(home_dir: Path | str | None = None) -> Path:
    """Blocking download of the pinned smart-turn model (~8 MB), verified.

    The bytes stream into a ``.part`` file next to the destination and are
    renamed into place only once the size and sha256 both match the pin; any
    failure removes the partial so a half file can never be mistaken for a
    model by ``make_detector``'s ``*.onnx`` glob.
    """
    pin = SMART_TURN_PIN
    dest = smart_turn_path(home_dir)
    _install_state["smart-turn"] = {"status": "downloading", "percent": 0.0}
    if dest.is_file():
        _install_state["smart-turn"] = {"status": "done", "percent": 100.0}
        return dest
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    try:
        digest = hashlib.sha256()
        done = 0
        with _hf_open(pin.url) as resp, tmp.open("wb") as f:
            while True:
                chunk = resp.read(1 << 18)
                if not chunk:
                    break
                done += len(chunk)
                if done > pin.size:
                    raise ValueError(
                        f"{pin.filename} is larger than the pinned {pin.size} bytes"
                    )
                digest.update(chunk)
                f.write(chunk)
                st = _install_state.get("smart-turn")
                if isinstance(st, dict):
                    st["percent"] = round(done * 100 / pin.size, 1)
        if done != pin.size:
            raise ValueError(f"{pin.filename}: expected {pin.size} bytes, got {done}")
        if digest.hexdigest() != pin.sha256:
            raise ValueError(f"{pin.filename}: sha256 does not match the pin")
        tmp.replace(dest)
        _install_state["smart-turn"] = {"status": "done", "percent": 100.0}
        return dest
    except Exception as exc:
        tmp.unlink(missing_ok=True)
        _install_state["smart-turn"] = {"status": "error", "error": str(exc)[:300]}
        raise


def install_smart_turn_background(home_dir: Path | str | None = None) -> bool:
    with _smart_turn_lock:
        st = _install_state.get("smart-turn")
        if isinstance(st, dict) and st.get("status") == "downloading":
            return False
        _install_state["smart-turn"] = {"status": "downloading", "percent": 0.0}
    t = threading.Thread(target=install_smart_turn, args=(home_dir,), daemon=True)
    t.start()
    return True

# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def voice_status(
    home_dir: Path | str | None = None, *, agent_gender: str | None = None
) -> dict[str, Any]:
    cfg = load_voice_settings(home_dir)
    tts_ok = tts_deps_available() and tts_installed(home_dir)
    stt_ok = stt_deps_available()
    turn_deps = smart_turn_deps_available()
    turn_installed = smart_turn_installed(home_dir)
    turn_ok = turn_deps and turn_installed
    reason_turn = (
        None
        if turn_ok
        else (
            "onnxruntime not installed (pip install remedy-ai[voice])"
            if not turn_deps
            else "smart-turn model not downloaded yet"
        )
    )
    reason_tts = (
        None
        if tts_ok
        else (
            "voice extra not installed (pip install remedy-ai[voice])"
            if not tts_deps_available()
            else "Kokoro model files not downloaded yet"
        )
    )
    return {
        "tts": {
            "available": tts_ok,
            "enabled": bool(cfg.get("tts_enabled", True)),
            "engine": "kokoro-82m" if tts_ok else None,
            "reason": reason_tts,
            "deps": tts_deps_available(),
            "installed": tts_installed(home_dir),
            "install": _install_state.get("tts"),
            "voice": voice_for_gender(agent_gender, cfg.get("voice_override")),
            "voices": list(KNOWN_VOICES),
            "fallback": "browser",
        },
        "stt": {
            "available": stt_ok,
            "enabled": bool(cfg.get("stt_enabled", True)),
            "engine": "faster-whisper" if stt_ok else None,
            "reason": (
                None
                if stt_ok
                else "voice extra not installed (pip install remedy-ai[voice])"
            ),
            "model": cfg.get("stt_model") or "small",
            "models": list(_VALID_STT),
            "installed": stt_installed(home_dir),
            "install": _install_state.get("stt"),
        },
        "smart_turn": {
            "available": turn_ok,
            "engine": "smart-turn-v3" if turn_ok else None,
            "reason": reason_turn,
            "deps": turn_deps,
            "installed": turn_installed,
            "install": _install_state.get("smart-turn"),
            "path": str(smart_turn_path(home_dir)) if turn_installed else None,
            "source": {
                "repo": SMART_TURN_PIN.repo,
                "revision": SMART_TURN_PIN.revision,
                "filename": SMART_TURN_PIN.filename,
                "licence": SMART_TURN_PIN.licence,
            },
            "fallback": "energy",
        },
        "settings": cfg,
    }
