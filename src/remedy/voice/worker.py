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


def op_probe(args: dict[str, Any]) -> dict[str, Any]:
    """Which engines can this runtime import right now?"""
    from remedy.voice import service as svc

    out: dict[str, Any] = {
        "tts": svc.tts_deps_available(),
        "stt": svc.stt_deps_available(),
        "smart_turn": svc.smart_turn_deps_available(),
    }
    try:
        from remedy.voice.chatterbox import chatterbox_deps_available

        out["hq"] = chatterbox_deps_available()
    except Exception:
        out["hq"] = False
    return out


def op_synthesize(args: dict[str, Any]) -> dict[str, Any] | None:
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
