"""High-quality TTS — Resemble AI Chatterbox (MIT).

Kokoro is the everyday / low-VRAM voice. Humans still hear it as a robot.
Chatterbox is the human-bar engine the telephony plan adopted, and the same
engine Grove uses when the owner turns on **High quality voice**.

Weights are not in the installer (~1.1 GB). They download when HQ is turned
on, into ``~/.remedy/voice/chatterbox/``. Tests never touch the network.
"""

from __future__ import annotations

import importlib
import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_engine_lock = threading.Lock()  # held while weights load (can be minutes)
_state_lock = threading.Lock()  # check-and-mark only — never blocks
_engine: Any | None = None
_install_state: dict[str, Any] = {}

#: Identity phrase — long enough for Chatterbox to clone (~6 s of speech).
_IDENTITY_LINE = (
    "Hi, this is Remedy. I am here to help you get things done on this "
    "computer, and I will keep this short so you can hear how I sound."
)


def chatterbox_home(home_dir: Path | str | None = None) -> Path:
    from remedy.voice.service import voice_home

    d = voice_home(home_dir) / "chatterbox"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _ready_path(home_dir: Path | str | None = None) -> Path:
    return chatterbox_home(home_dir) / "ready.json"


def _hf_home(home_dir: Path | str | None = None) -> Path:
    d = chatterbox_home(home_dir) / "hf"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _identity_dir(home_dir: Path | str | None = None) -> Path:
    d = chatterbox_home(home_dir) / "identity"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _pulse_hq(stop: threading.Event) -> None:
    """Keep the title-bar percent moving while Hugging Face has no callback."""
    while not stop.wait(1.2):
        st = _install_state.get("chatterbox")
        if not isinstance(st, dict) or st.get("status") != "downloading":
            return
        p = float(st.get("percent") or 36.0)
        if p < 92.0:
            st["percent"] = round(min(92.0, p + 1.5), 1)


def _managed() -> bool:
    from remedy.voice.runtime import use_managed_runtime

    return use_managed_runtime()


def chatterbox_deps_available() -> bool:
    if _managed():
        from remedy.voice.runtime import pack_installed, runtime_ready

        return runtime_ready() and pack_installed("hq")
    try:
        import chatterbox.tts  # noqa: F401

        return True
    except Exception:
        return False


def chatterbox_installed(home_dir: Path | str | None = None) -> bool:
    p = _ready_path(home_dir)
    if not p.is_file():
        return False
    try:
        raw = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return bool(raw.get("ok"))


def chatterbox_ready(home_dir: Path | str | None = None) -> bool:
    """True when a speak request can use Chatterbox without installing."""
    if _engine is not None:
        return True
    return chatterbox_deps_available() and chatterbox_installed(home_dir)


def chatterbox_install_state() -> dict[str, Any] | None:
    st = _install_state.get("chatterbox")
    return st if isinstance(st, dict) else None


def _skip_network() -> bool:
    if os.environ.get("REMEDY_ENSURE_ASSETS") == "1":
        return False
    if os.environ.get("REMEDY_NO_FIRST_RUN_DOWNLOAD") == "1":
        return True
    return bool(os.environ.get("PYTEST_CURRENT_TEST"))


def _device() -> str:
    try:
        import torch

        if torch.cuda.is_available():
            return "cuda"
    except Exception:
        pass
    return "cpu"


def _ensure_package(home_dir: Path | str | None = None) -> bool:
    if chatterbox_deps_available():
        return True
    if _skip_network():
        return False
    try:
        _install_state["chatterbox"] = {
            "status": "downloading",
            "percent": 5.0,
            "message": "Installing the high-quality voice pack",
        }
        from remedy.voice.service import run_pip_packages

        if _managed():
            # Desktop: the pack lands in the managed runtime, downloading
            # the runtime itself first if this is the owner's first pack.
            from remedy.voice.runtime import mark_pack
            from remedy.voice.service import _PACK_BASE_PACKAGES, _runtime_python

            py = _runtime_python(home_dir, "chatterbox")
            run_pip_packages(
                _PACK_BASE_PACKAGES + ("chatterbox-tts",),
                _install_state,
                "chatterbox",
                cap=35.0,
                python=py,
            )
            from remedy.voice.bridge import get_bridge

            get_bridge(home_dir).stop()  # stale imports from before pip
            mark_pack("hq", bool(get_bridge(home_dir).probe().get("hq")), home_dir)
        else:
            run_pip_packages(
                ("chatterbox-tts",),
                _install_state,
                "chatterbox",
                cap=35.0,
            )
            importlib.invalidate_caches()
    except Exception as exc:
        from remedy.voice.service import _owner_pack_error

        _install_state["chatterbox"] = {
            "status": "error",
            "error": _owner_pack_error(exc, what="High-quality voice"),
        }
        return False
    if not chatterbox_deps_available():
        _install_state["chatterbox"] = {
            "status": "error",
            "error": "High-quality voice did not finish downloading.",
        }
        return False
    return True


def _mark_ready(home_dir: Path | str | None, *, sr: int) -> None:
    from remedy.core.atomic_json import write_text_atomic

    write_text_atomic(
        _ready_path(home_dir),
        json.dumps(
            {"ok": True, "engine": "chatterbox", "sample_rate": int(sr)},
            indent=2,
        )
        + "\n",
    )


def get_chatterbox_engine(home_dir: Path | str | None = None) -> Any | None:
    """Lazy ChatterboxTTS, downloading weights into ~/.remedy/voice/chatterbox."""
    global _engine
    with _engine_lock:
        if _engine is not None:
            return _engine
        if not _ensure_package(home_dir):
            return None
        os.environ.setdefault("HF_HOME", str(_hf_home(home_dir)))
        os.environ.setdefault("HUGGINGFACE_HUB_CACHE", str(_hf_home(home_dir) / "hub"))
        try:
            from chatterbox.tts import ChatterboxTTS

            _install_state["chatterbox"] = {
                "status": "downloading",
                "percent": 36.0,
                "message": "Downloading Chatterbox",
            }
            stop = threading.Event()
            pulse = threading.Thread(
                target=_pulse_hq,
                args=(stop,),
                daemon=True,
            )
            pulse.start()
            try:
                model = ChatterboxTTS.from_pretrained(device=_device())
            finally:
                stop.set()
            _engine = model
            sr = int(getattr(model, "sr", 24_000) or 24_000)
            _mark_ready(home_dir, sr=sr)
            _install_state["chatterbox"] = {"status": "done", "percent": 100.0}
            return _engine
        except Exception as exc:
            logger.warning("chatterbox: load failed: %s", exc)
            _install_state["chatterbox"] = {
                "status": "error",
                "error": str(exc)[:300],
            }
            return None


def install_chatterbox(home_dir: Path | str | None = None) -> None:
    """Blocking: pip (if needed) + Hugging Face weights."""
    if _skip_network() and os.environ.get("REMEDY_ENSURE_ASSETS") != "1":
        _install_state["chatterbox"] = {
            "status": "skipped",
            "reason": "tests_or_disabled",
        }
        return
    _install_state["chatterbox"] = {"status": "downloading", "percent": 1.0}
    if _managed():
        _install_managed(home_dir)
        return
    if get_chatterbox_engine(home_dir) is None:
        if _install_state.get("chatterbox", {}).get("status") != "error":
            _install_state["chatterbox"] = {
                "status": "error",
                "error": "High-quality voice could not be loaded.",
            }
        raise RuntimeError("chatterbox install failed")


def _install_managed(home_dir: Path | str | None) -> None:
    """Desktop: pip the pack into the runtime, then let the worker pull weights."""
    from remedy.voice.bridge import WorkerError, get_bridge
    from remedy.voice.service import _owner_pack_error

    if not _ensure_package(home_dir):
        raise RuntimeError("chatterbox install failed")
    _install_state["chatterbox"] = {
        "status": "downloading",
        "percent": 36.0,
        "message": "Downloading Chatterbox",
    }
    stop = threading.Event()
    pulse = threading.Thread(target=_pulse_hq, args=(stop,), daemon=True)
    pulse.start()
    try:
        out = get_bridge(home_dir).warm_hq()
    except WorkerError as exc:
        _install_state["chatterbox"] = {
            "status": "error",
            "error": _owner_pack_error(exc, what="High-quality voice"),
        }
        raise RuntimeError("chatterbox install failed") from exc
    finally:
        stop.set()
    st = out.get("state") if isinstance(out, dict) else None
    if out.get("loaded") if isinstance(out, dict) else False:
        _install_state["chatterbox"] = {"status": "done", "percent": 100.0}
        return
    err = str(st.get("error") or "") if isinstance(st, dict) else ""
    _install_state["chatterbox"] = {
        "status": "error",
        "error": _owner_pack_error(
            RuntimeError(err or "not loaded"), what="High-quality voice"
        ),
    }
    raise RuntimeError("chatterbox install failed")


def install_chatterbox_background(home_dir: Path | str | None = None) -> bool:
    # Only the cheap state lock here: _engine_lock is held by the worker for
    # the whole download and this is called from async routes.
    with _state_lock:
        st = _install_state.get("chatterbox")
        if isinstance(st, dict) and st.get("status") == "downloading":
            return False
        if _engine is not None or chatterbox_installed(home_dir):
            _install_state["chatterbox"] = {"status": "done", "percent": 100.0}
            return False
        _install_state["chatterbox"] = {"status": "downloading", "percent": 0.0}
    t = threading.Thread(target=install_chatterbox, args=(home_dir,), daemon=True)
    t.start()
    return True


def _floats(wav: Any) -> Any:
    """Torch / numpy / list → 1-D float sequence encode_wav understands."""
    if hasattr(wav, "detach"):
        wav = wav.detach().cpu().float()
    if hasattr(wav, "squeeze"):
        wav = wav.squeeze()
    if hasattr(wav, "reshape"):
        wav = wav.reshape(-1)
    return wav


def identity_prompt_path(
    gender: str | None, home_dir: Path | str | None = None
) -> Path | None:
    """Existing identity clip for this gender, or None (use Chatterbox default)."""
    from remedy.voice.identity import reference_wav

    owned = reference_wav(gender, home_dir)
    if owned is not None:
        return owned
    try:
        from remedy.core.agent_identity import normalize_agent_gender

        g = normalize_agent_gender(gender)
    except Exception:
        g = (gender or "female").strip().lower() or "female"
    p = _identity_dir(home_dir) / f"{g}.wav"
    if p.is_file() and p.stat().st_size > 64:
        return p
    return None


def _bootstrap_identity(
    gender: str | None, home_dir: Path | str | None = None
) -> Path | None:
    """One-time: record a gender-matched clip with Kokoro for Chatterbox to clone.

    Chatterbox's built-in speaker is the female default. Male HQ needs a prompt.
    Cloning a short Kokoro line is better than speaking as the wrong gender.
    """
    try:
        from remedy.core.agent_identity import normalize_agent_gender

        g = normalize_agent_gender(gender)
    except Exception:
        g = (gender or "female").strip().lower() or "female"
    dest = _identity_dir(home_dir) / f"{g}.wav"
    if dest.is_file() and dest.stat().st_size > 64:
        return dest
    if g == "female":
        return None
    try:
        from remedy.voice.service import (
            encode_wav,
            get_tts_engine,
            voice_for_gender,
        )

        kokoro = get_tts_engine(home_dir)
        if kokoro is None:
            return None
        vid = voice_for_gender(g)
        samples, sr = kokoro.create(_IDENTITY_LINE, voice=vid, speed=1.0)
        dest.write_bytes(encode_wav(samples, int(sr)))
        try:
            from remedy.voice.identity import set_reference

            set_reference(dest, home_dir)
        except Exception:
            pass
        return dest
    except Exception as exc:
        logger.info("chatterbox: identity bootstrap skipped: %s", exc)
        return None


def synthesize(
    text: str,
    *,
    gender: str | None = None,
    home_dir: Path | str | None = None,
) -> tuple[bytes, int] | None:
    """Text → (wav_bytes, sample_rate) via Chatterbox, or None."""
    from remedy.voice.service import encode_wav, speakable_text

    clean = speakable_text(text)
    if not clean:
        return None
    model = get_chatterbox_engine(home_dir)
    if model is None:
        return None
    prompt = identity_prompt_path(gender, home_dir)
    if prompt is None:
        prompt = _bootstrap_identity(gender, home_dir)
    kwargs: dict[str, Any] = {}
    if prompt is not None:
        kwargs["audio_prompt_path"] = str(prompt)
    try:
        try:
            wav = model.generate(clean, **kwargs)
        except TypeError as exc:
            # Only an unknown-kwarg TypeError means "this build has no
            # audio_prompt_path"; anything else is a real failure.
            if not kwargs or "audio_prompt_path" not in str(exc):
                raise
            logger.info("chatterbox: no audio_prompt_path support; plain voice")
            wav = model.generate(clean)
    except Exception as exc:
        logger.warning("chatterbox: generate failed: %s", exc)
        return None
    sr = int(getattr(model, "sr", 24_000) or 24_000)
    return encode_wav(_floats(wav), sr), sr


def _hardware_note() -> str | None:
    """Plain words when this PC cannot hold the GPU human-bar."""
    try:
        from remedy.runtime.gpu_probe import probe_primary_vram

        _nvidia, total, _free, name, vendor = probe_primary_vram()
    except Exception:
        return None
    if total >= 4000:
        return None
    if total <= 0:
        return (
            "This computer has no dedicated GPU, so the high-quality voice "
            "runs on the CPU and takes longer. It will still sound like a person."
        )
    label = name or vendor or "this GPU"
    return (
        f"{label} has about {total} MB of memory, under the 4 GB the "
        "high-quality voice prefers. It will run slower, not quieter."
    )


def hq_status(home_dir: Path | str | None = None) -> dict[str, Any]:
    deps = chatterbox_deps_available()
    installed = chatterbox_installed(home_dir)
    ready = deps and (installed or _engine is not None)
    reason = None
    hint = None
    hardware = _hardware_note()
    if not ready:
        if not deps:
            reason = "High-quality voice is not on this computer yet."
            if _managed():
                from remedy.voice.runtime import unsupported_reason

                reason = unsupported_reason() or reason
            else:
                hint = "pip install chatterbox-tts"
        else:
            reason = "High-quality voice downloads when you turn it on (~1.1 GB)."
        if hardware:
            reason = f"{reason} {hardware}"
    elif hardware:
        reason = hardware
    return {
        "available": ready,
        "engine": "chatterbox" if ready else None,
        "deps": deps,
        "installed": installed,
        "install": chatterbox_install_state(),
        "reason": reason,
        "hint": hint,
        "approx_mb": 1100,
        "licence": "MIT",
        "source": "Resemble AI",
        "fallback": "kokoro",
        "hardware": hardware,
    }
