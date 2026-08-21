"""Voice worker — runs the engines inside the managed runtime.

``python -m remedy.voice.worker`` reads one JSON request per line on stdin
and writes one JSON reply per line on stdout. It imports the ordinary
:mod:`remedy.voice.service` code — the same source the sidecar bundles — so
Kokoro / whisper / Chatterbox / smart-turn behave exactly as in dev; only
the process is different.

Protocol (one object per line)::

    → {"id": 1, "op": "synthesize", "args": {"text": "hi", "gender": "female"}}
    ← {"id": 1, "ok": true, "result": {"wav_b64": "...", "sample_rate": 24000}}
    ← {"id": 1, "ok": false, "error": "plain words"}

The worker never logs to stdout (that is the wire); logging goes to stderr,
which the bridge forwards into the sidecar log.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import sys
import traceback
from pathlib import Path
from typing import Any

logger = logging.getLogger("remedy.voice.worker")


def _home(args: dict[str, Any]) -> str | None:
    h = args.get("home_dir")
    return str(h) if h else None


# ---------------------------------------------------------------------------
# Ops
# ---------------------------------------------------------------------------


def op_ping(args: dict[str, Any]) -> dict[str, Any]:
    import platform

    return {"pid": os.getpid(), "python": platform.python_version()}


def _lane() -> str:
    return os.environ.get("REMEDY_VOICE_LANE") or "voice"


def op_probe(args: dict[str, Any]) -> dict[str, Any]:
    """Which engines can this lane import right now?

    Only this lane's own engines are imported — importing whisper into the
    hq lane (or torch into the voice lane) is exactly the DLL clash the
    lanes exist to avoid.
    """
    out: dict[str, Any] = {"lane": _lane()}
    if _lane() == "hq":
        try:
            import torch

            out["torch"] = str(torch.__version__)
            out["cuda"] = bool(torch.cuda.is_available())
        except Exception:
            out["torch"] = ""
            out["cuda"] = False
        try:
            from remedy.voice.chatterbox import chatterbox_deps_available

            out["hq"] = chatterbox_deps_available()
        except Exception:
            out["hq"] = False
        return out
    from remedy.voice import service as svc

    out["tts"] = svc.tts_deps_available()
    out["stt"] = svc.stt_deps_available()
    out["smart_turn"] = svc.smart_turn_deps_available()
    return out


def op_synthesize(args: dict[str, Any]) -> dict[str, Any] | None:
    """Speak. In the hq lane this is Chatterbox directly (no Kokoro fallback
    here — the sidecar falls back to the voice lane itself)."""
    if _lane() == "hq":
        from remedy.voice.chatterbox import synthesize as hq_synthesize

        hq_out = hq_synthesize(
            str(args.get("text") or ""), gender=args.get("gender"), home_dir=_home(args)
        )
        if hq_out is None:
            return None
        wav, sr = hq_out
        return {"wav_b64": base64.b64encode(wav).decode("ascii"), "sample_rate": int(sr)}
    from remedy.voice.service import synthesize

    out = synthesize(
        str(args.get("text") or ""),
        gender=args.get("gender"),
        voice=args.get("voice"),
        speed=args.get("speed"),
        home_dir=_home(args),
    )
    if out is None:
        return None
    wav, sr = out
    return {"wav_b64": base64.b64encode(wav).decode("ascii"), "sample_rate": int(sr)}


def op_transcribe(args: dict[str, Any]) -> dict[str, Any] | None:
    from remedy.voice.service import transcribe_file

    return transcribe_file(
        Path(str(args["path"])),
        language=args.get("language"),
        home_dir=_home(args),
    )


def op_warm_stt(args: dict[str, Any]) -> dict[str, Any]:
    from remedy.voice import service as svc

    model = svc.get_stt_model(_home(args))
    st = svc._install_state.get("stt")
    return {"loaded": model is not None, "state": st if isinstance(st, dict) else None}


def op_warm_hq(args: dict[str, Any]) -> dict[str, Any]:
    """Load Chatterbox (downloads weights on first use) and report state."""
    from remedy.voice import chatterbox as hq

    engine = hq.get_chatterbox_engine(_home(args))
    st = hq._install_state.get("chatterbox")
    return {"loaded": engine is not None, "state": st if isinstance(st, dict) else None}


_hq_warm_thread: dict[str, Any] = {}


def op_warm_hq_start(args: dict[str, Any]) -> dict[str, Any]:
    """Begin loading Chatterbox in the background; poll ``hq_state``.

    The wire is one request at a time, so a blocking warm would hide its
    own progress. This returns at once and the sidecar mirrors
    ``_install_state["chatterbox"]`` (real byte counts) until done/error.
    """
    import threading

    from remedy.voice import chatterbox as hq

    t = _hq_warm_thread.get("t")
    if t is not None and t.is_alive():
        return {"started": False}
    home = _home(args)
    hq._install_state["chatterbox"] = {"status": "downloading", "percent": 36.0, "message": "Downloading Chatterbox"}

    gender = args.get("gender")

    def _run() -> None:
        try:
            if hq.get_chatterbox_engine(home) is None:
                st = hq._install_state.get("chatterbox")
                if not (isinstance(st, dict) and st.get("status") == "error"):
                    hq._install_state["chatterbox"] = {"status": "error", "error": "not loaded"}
                return
            # First utterance pays for kernel warm-up and the reference
            # conditionals (~15 s); do it now, quietly, not on her first reply.
            if gender:
                hq.synthesize("Ready.", gender=str(gender), home_dir=home)
        except Exception as exc:  # noqa: BLE001
            hq._install_state["chatterbox"] = {"status": "error", "error": str(exc)[:300]}

    t = threading.Thread(target=_run, daemon=True)
    _hq_warm_thread["t"] = t
    t.start()
    return {"started": True}


def op_hq_state(args: dict[str, Any]) -> dict[str, Any]:
    from remedy.voice import chatterbox as hq

    st = hq._install_state.get("chatterbox")
    return {"state": st if isinstance(st, dict) else None}


_turn_scorers: dict[str, Any] = {}


def op_turn_score(args: dict[str, Any]) -> dict[str, Any]:
    """Smart-turn probability for a window of line audio."""
    from remedy.voice.realtime.turn import SmartTurnDetector

    model_path = str(args.get("model_path") or "")
    det = _turn_scorers.get(model_path)
    if det is None:
        det = SmartTurnDetector(model_path=model_path)
        _turn_scorers[model_path] = det
    pcm = base64.b64decode(str(args.get("pcm_b64") or ""))
    return {"score": float(det.score(pcm))}


OPS = {
    "ping": op_ping,
    "probe": op_probe,
    "synthesize": op_synthesize,
    "transcribe": op_transcribe,
    "warm_stt": op_warm_stt,
    "warm_hq": op_warm_hq,
    "warm_hq_start": op_warm_hq_start,
    "hq_state": op_hq_state,
    "turn_score": op_turn_score,
}


# ---------------------------------------------------------------------------
# Loop
# ---------------------------------------------------------------------------


def handle(line: str) -> dict[str, Any]:
    try:
        req = json.loads(line)
    except json.JSONDecodeError as exc:
        return {"id": None, "ok": False, "error": f"bad request: {exc}"}
    rid = req.get("id")
    op = str(req.get("op") or "")
    fn = OPS.get(op)
    if fn is None:
        return {"id": rid, "ok": False, "error": f"unknown op {op!r}"}
    try:
        return {"id": rid, "ok": True, "result": fn(req.get("args") or {})}
    except Exception as exc:  # noqa: BLE001 — every failure goes back on the wire
        logger.warning("voice worker %s failed: %s\n%s", op, exc, traceback.format_exc())
        return {"id": rid, "ok": False, "error": str(exc)[:400]}


def main() -> int:
    os.environ["REMEDY_VOICE_WORKER"] = "1"
    if _lane() == "hq":
        # torch must own cuDNN in this process before anything else loads.
        try:
            import torch  # noqa: F401
        except Exception as exc:  # noqa: BLE001 — reported per op later
            logging.getLogger("remedy.voice.worker").warning("hq lane: torch import failed: %s", exc)
    logging.basicConfig(
        stream=sys.stderr,
        level=os.environ.get("REMEDY_VOICE_WORKER_LOG", "WARNING"),
        format="%(levelname)s %(name)s: %(message)s",
    )
    out = sys.stdout
    # stdout is the wire: no buffering surprises, no stray prints.
    sys.stdout = sys.stderr
    for raw in sys.stdin:
        line = raw.strip()
        if not line:
            continue
        reply = handle(line)
        out.write(json.dumps(reply, ensure_ascii=True) + "\n")
        out.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
