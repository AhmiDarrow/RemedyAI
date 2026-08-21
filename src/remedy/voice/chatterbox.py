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
import sys
import threading
import time
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


# chatterbox-tts pins torch 2.6.0; these are the same version built for CUDA 12.4.
_CUDA_TORCH_INDEX = "https://download.pytorch.org/whl/cu124"
_CUDA_TORCH_PACKAGES = ("torch==2.6.0+cu124", "torchaudio==2.6.0+cu124")


def _wants_cuda_torch() -> bool:
    """An NVIDIA card on a platform the cu124 wheels cover."""
    if sys.platform not in ("win32", "linux"):
        return False
    if os.environ.get("REMEDY_VOICE_CPU_ONLY") == "1":
        return False
    try:
        from remedy.runtime.gpu_probe import probe_primary_vram

        is_nvidia, total_mb, _free, _name, _vendor = probe_primary_vram()
    except Exception:
        return False
    return bool(is_nvidia and total_mb >= 4096)


def _needs_cuda_upgrade(home_dir: Path | str | None) -> bool:
    """HQ is installed on a CPU torch while this machine has an NVIDIA card."""
    if not _managed() or not _wants_cuda_torch():
        return False
    try:
        from remedy.voice.bridge import LANE_HQ, get_bridge

        probe = get_bridge(home_dir, LANE_HQ).probe()
    except Exception:
        return False
    return bool(probe.get("hq")) and "+cpu" in str(probe.get("torch") or "")


def _ensure_package(home_dir: Path | str | None = None) -> bool:
    if chatterbox_deps_available() and not _needs_cuda_upgrade(home_dir):
        return True
    if _skip_network():
        return chatterbox_deps_available()
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
            # PyPI's Windows/Linux torch is CPU-only. With an NVIDIA card the
            # human-bar voice is ~20x faster on CUDA, so put the CUDA build
            # in *first*: chatterbox-tts then finds torch==2.6.0 satisfied and
            # never pulls the CPU wheel only to have it replaced.
            floor = 5.0
            if _wants_cuda_torch():
                _install_state["chatterbox"]["message"] = "Fetching the GPU build of torch"
                run_pip_packages(
                    _CUDA_TORCH_PACKAGES,
                    _install_state,
                    "chatterbox",
                    cap=20.0,
                    python=py,
                    floor=floor,
                    extra_args=("--index-url", _CUDA_TORCH_INDEX),
                    label="the GPU build of torch",
                )
                floor = 20.0
            run_pip_packages(
                _PACK_BASE_PACKAGES + ("chatterbox-tts",),
                _install_state,
                "chatterbox",
                cap=35.0,
                python=py,
                floor=floor,
                label="the high-quality voice pack",
            )
            from remedy.voice.bridge import LANE_HQ, get_bridge, stop_lane

            stop_lane(home_dir, LANE_HQ)  # stale imports from before pip
            mark_pack("hq", bool(get_bridge(home_dir, LANE_HQ).probe().get("hq")), home_dir)
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


# The files ChatterboxTTS.from_pretrained fetches, in its order. Fetching
# them ourselves first gives the owner a real byte count instead of a pulse;
# from_pretrained then finds every file already in the cache.
_HQ_REPO = "ResembleAI/chatterbox"
_HQ_FILES = ("ve.safetensors", "t3_cfg.safetensors", "s3gen.safetensors", "tokenizer.json", "conds.pt")
_HQ_DL_FLOOR, _HQ_DL_CAP = 36.0, 92.0


def _prefetch_weights() -> None:
    """Download the HQ weights with progress in _install_state["chatterbox"].

    Sizes come from the hub's metadata; bytes are read off the cache as
    each file lands (the hub writes to a .incomplete blob next to the final
    one), so the bar moves during a 1 GB file, not only between files.
    """
    from huggingface_hub import HfApi, hf_hub_download

    st = _install_state.get("chatterbox")
    if not isinstance(st, dict):
        return
    sizes: dict[str, int] = {}
    try:
        info = HfApi().model_info(_HQ_REPO, files_metadata=True)
        for sib in info.siblings or []:
            if sib.rfilename in _HQ_FILES and sib.size:
                sizes[sib.rfilename] = int(sib.size)
    except Exception:
        sizes = {}
    total = sum(sizes.get(f, 0) for f in _HQ_FILES) or 0
    done_before = 0
    stop = threading.Event()

    def _watch(fname: str, size: int, start: int) -> None:
        # Poll the hub cache for this file's growing blob.
        cache = Path(os.environ.get("HUGGINGFACE_HUB_CACHE") or "")
        while not stop.wait(0.5):
            got = 0
            try:
                for p in cache.rglob("*.incomplete"):
                    got = max(got, p.stat().st_size)
            except OSError:
                got = 0
            if total:
                frac = min(1.0, (start + min(got, size)) / total)
                st["percent"] = round(_HQ_DL_FLOOR + (_HQ_DL_CAP - _HQ_DL_FLOOR) * frac, 1)
            st["message"] = (
                f"Downloading Chatterbox · {(start + min(got, size)) / 2**30:.2f} of {total / 2**30:.2f} GB"
                if total
                else f"Downloading Chatterbox · {fname}"
            )

    for fname in _HQ_FILES:
        size = sizes.get(fname, 0)
        stop.clear()
        w = threading.Thread(target=_watch, args=(fname, size, done_before), daemon=True)
        w.start()
        try:
            hf_hub_download(repo_id=_HQ_REPO, filename=fname)
        finally:
            stop.set()
        done_before += size
        if total:
            st["percent"] = round(_HQ_DL_FLOOR + (_HQ_DL_CAP - _HQ_DL_FLOOR) * min(1.0, done_before / total), 1)
    st["message"] = "Loading Chatterbox"


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
                "percent": _HQ_DL_FLOOR,
                "message": "Downloading Chatterbox",
            }
            _prefetch_weights()
            model = ChatterboxTTS.from_pretrained(device=_device())
            _engine = model
            # Chatterbox's generate() is stateful: a prompt replaces
            # model.conds and a later call *without* one silently reuses
            # them. Keep the built-in speaker so "female" can always be
            # restored exactly, whatever was spoken last.
            global _default_conds
            _default_conds = getattr(model, "conds", None)
            _prompt_conds.clear()
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
    from remedy.voice.bridge import LANE_HQ, WorkerError, get_bridge
    from remedy.voice.service import _owner_pack_error

    if not _ensure_package(home_dir):
        raise RuntimeError("chatterbox install failed")
    _install_state["chatterbox"] = {
        "status": "downloading",
        "percent": _HQ_DL_FLOOR,
        "message": "Downloading Chatterbox",
    }
    bridge = get_bridge(home_dir, LANE_HQ)
    try:
        bridge.call("warm_hq_start", timeout=60)
        # Mirror the worker's own progress (real bytes) until it settles.
        deadline = time.monotonic() + 3 * 3600
        t_start = time.monotonic()
        while time.monotonic() < deadline:
            time.sleep(1.0)
            st = bridge.hq_state() or {}
            status = str(st.get("status") or "")
            if status == "downloading":
                cur = _install_state.get("chatterbox")
                if isinstance(cur, dict):
                    if st.get("percent") is not None:
                        cur["percent"] = float(st.get("percent") or 0.0)
                    msg = str(st.get("message") or "Downloading Chatterbox")
                    # The load step has no bytes to show; keep the clock moving.
                    if msg.startswith("Loading"):
                        secs = int(time.monotonic() - t_start)
                        msg = f"{msg} · {secs // 60}m{secs % 60:02d}s" if secs >= 60 else f"{msg} · {secs}s"
                    cur["message"] = msg
                continue
            if status == "done":
                _install_state["chatterbox"] = {"status": "done", "percent": 100.0}
                return
            if status == "error":
                raise RuntimeError(str(st.get("error") or "not loaded"))
        raise TimeoutError("High-quality voice took longer than three hours")
    except WorkerError as exc:
        _install_state["chatterbox"] = {
            "status": "error",
            "error": _owner_pack_error(exc, what="High-quality voice"),
        }
        raise RuntimeError("chatterbox install failed") from exc
    except (RuntimeError, TimeoutError) as exc:
        _install_state["chatterbox"] = {
            "status": "error",
            "error": _owner_pack_error(exc, what="High-quality voice"),
        }
        raise RuntimeError("chatterbox install failed") from exc


def install_chatterbox_background(home_dir: Path | str | None = None) -> bool:
    # Only the cheap state lock here: _engine_lock is held by the worker for
    # the whole download and this is called from async routes.
    with _state_lock:
        st = _install_state.get("chatterbox")
        if isinstance(st, dict) and st.get("status") == "downloading":
            return False
        if (_engine is not None or chatterbox_installed(home_dir)) and not _needs_cuda_upgrade(
            home_dir
        ):
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

    Chatterbox's built-in speaker is not a dependable voice of either gender
    (the same line measured 160 Hz one run and 111 Hz the next), so *every*
    gender gets an explicit reference: a short Kokoro line in the matching
    voice. Chatterbox keeps the timbre and adds its human prosody; the
    voice is the same every time and changes when the owner changes it.
    """
    try:
        from remedy.core.agent_identity import normalize_agent_gender

        g = normalize_agent_gender(gender)
    except Exception:
        g = (gender or "female").strip().lower() or "female"
    dest = _identity_dir(home_dir) / f"{g}.wav"
    if dest.is_file() and dest.stat().st_size > 64:
        return dest
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
        # Deliberately *not* set_reference(): this is a gender stand-in, not
        # her identity. Recording it as the reference made every gender —
        # including the built-in female — speak from the male clip.
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
    try:
        with _engine_lock:
            _select_speaker(model, prompt)
            wav = model.generate(clean)
    except Exception as exc:
        logger.warning("chatterbox: generate failed: %s", exc)
        return None
    sr = int(getattr(model, "sr", 24_000) or 24_000)
    return encode_wav(_floats(wav), sr), sr


#: The built-in speaker's conditionals, captured at load.
_default_conds: Any = None
#: Prepared conditionals per identity clip (path + mtime) — prepare once.
_prompt_conds: dict[tuple[str, float], Any] = {}


def _select_speaker(model: Any, prompt: Path | None) -> None:
    """Make *this* utterance's speaker explicit, never "whatever was last".

    No prompt → the built-in (female) speaker, restored from the snapshot
    taken at load. A prompt → its conditionals, prepared once per file and
    cached, so switching gender back and forth costs nothing after the
    first time.
    """
    if prompt is None:
        if _default_conds is not None:
            model.conds = _default_conds
        return
    try:
        key = (str(prompt), prompt.stat().st_mtime)
    except OSError:
        key = (str(prompt), 0.0)
    conds = _prompt_conds.get(key)
    if conds is None:
        model.prepare_conditionals(str(prompt))
        conds = model.conds
        if len(_prompt_conds) >= 4:
            _prompt_conds.clear()
        _prompt_conds[key] = conds
    else:
        model.conds = conds


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
