"""Opt-in raw LLM request trace — evaluation + distillation data.

Writes one JSONL record per ReAct step containing the **exact** body handed to
the provider: assembled system prompt, tool schemas after local slimming, the
full message history (assistant ``tool_calls`` and ``tool`` results included),
and the sampling knobs. Because every step re-sends the whole conversation, the
final record of a turn is a complete trajectory — directly usable as a
supervised fine-tuning sample when the turn was driven by a strong model.

Off unless ``REMEDY_LLM_TRACE_DIR`` is set, so normal product runs never pay
the write and never leave conversation text on disk. The harness
(``scripts/rig``) sets it per run inside a disposable sandbox.

Records are local-only and unredacted by design: they must match what the model
actually saw, or they are useless as training data. Point the env var at a
throwaway directory, never at a shared one.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

_lock = threading.Lock()
_seq = 0

# A single ReAct step with 48 tool schemas and a long history runs ~150 KB.
# Cap per-file growth so a runaway loop cannot fill the disk during a soak.
_MAX_BYTES = 512 * 1024 * 1024


def trace_dir() -> Path | None:
    """Resolved trace directory, or None when tracing is off."""
    raw = (os.environ.get("REMEDY_LLM_TRACE_DIR") or "").strip()
    if not raw:
        return None
    try:
        p = Path(raw).expanduser()
        p.mkdir(parents=True, exist_ok=True)
        return p
    except OSError:
        return None


def enabled() -> bool:
    return trace_dir() is not None


def record_request(
    body: dict[str, Any],
    *,
    provider: str = "",
    model: str = "",
    step: int = 0,
    session_id: str = "",
) -> None:
    """Append one request record. Never raises — tracing must not break a turn."""
    global _seq
    d = trace_dir()
    if d is None or not isinstance(body, dict):
        return
    try:
        sid = (session_id or "session").replace("/", "_").replace("\\", "_")[:64]
        path = d / f"{sid}.jsonl"
        with _lock:
            if path.exists() and path.stat().st_size > _MAX_BYTES:
                return
            _seq += 1
            rec = {
                "seq": _seq,
                "ts": time.time(),
                "provider": provider,
                "model": model,
                "step": int(step),
                "session_id": session_id,
                "body": body,
            }
            line = json.dumps(rec, default=str, ensure_ascii=False)
            with path.open("a", encoding="utf-8") as fh:
                fh.write(line + "\n")
    except Exception:
        # Diagnostics must never take the turn down.
        return
