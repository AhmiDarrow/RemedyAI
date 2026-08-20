"""Owner-voice clone — borrowed for a named task, never worn as a default.

Grant names a task, expires, and is revocable in one sentence. Samples live
under ``~/.remedy/voice/clone/`` with owner-only permissions. DPAPI wrapping
is the same idea as provider keys; the grant metadata is not a secret.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from remedy.core.atomic_json import write_text_atomic

logger = logging.getLogger(__name__)


def _dir(home_dir: Path | str | None = None) -> Path:
    from remedy.voice.service import voice_home

    d = voice_home(home_dir) / "clone"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _grant_path(home_dir: Path | str | None = None) -> Path:
    return _dir(home_dir) / "grant.json"


def _wav_path(home_dir: Path | str | None = None) -> Path:
    return _dir(home_dir) / "sample.wav"


@dataclass(frozen=True, slots=True)
class CloneGrant:
    task: str
    expires_at: float
    granted_at: float
    path: str

    @property
    def live(self) -> bool:
        return time.time() < self.expires_at and Path(self.path).is_file()

    def public(self) -> dict[str, Any]:
        return {
            "task": self.task,
            "expires_at": self.expires_at,
            "granted_at": self.granted_at,
            "live": self.live,
        }


def read(home_dir: Path | str | None = None) -> CloneGrant | None:
    try:
        raw = json.loads(_grant_path(home_dir).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict) or not raw.get("task"):
        return None
    try:
        g = CloneGrant(
            task=str(raw["task"]),
            expires_at=float(raw.get("expires_at") or 0),
            granted_at=float(raw.get("granted_at") or 0),
            path=str(raw.get("path") or _wav_path(home_dir)),
        )
    except (TypeError, ValueError):
        return None
    if not g.live:
        return None
    return g


def grant(
    task: str,
    wav: bytes,
    *,
    ttl_s: float = 3600.0,
    home_dir: Path | str | None = None,
) -> CloneGrant:
    """Store a sample for *task* only. Replaces any previous grant."""
    name = (task or "").strip()
    if not name:
        raise ValueError("a clone grant has to name the task")
    if not wav or len(wav) < 64:
        raise ValueError("clone sample is empty")
    dest = _wav_path(home_dir)
    dest.write_bytes(wav)
    with contextlib.suppress(OSError):
        os.chmod(dest, 0o600)
    now = time.time()
    g = CloneGrant(
        task=name,
        expires_at=now + max(60.0, float(ttl_s)),
        granted_at=now,
        path=str(dest),
    )
    write_text_atomic(
        _grant_path(home_dir),
        json.dumps(
            {
                "task": g.task,
                "expires_at": g.expires_at,
                "granted_at": g.granted_at,
                "path": g.path,
            },
            indent=2,
        )
        + "\n",
    )
    logger.info("voice clone granted for task %r until %s", g.task, g.expires_at)
    return g


def revoke(home_dir: Path | str | None = None) -> None:
    """Drop the grant and the sample. Safe to call twice."""
    for p in (_grant_path(home_dir), _wav_path(home_dir)):
        with contextlib.suppress(OSError):
            p.unlink()
    logger.info("voice clone revoked")


def sample_path(home_dir: Path | str | None = None) -> Path | None:
    g = read(home_dir)
    if g is None:
        return None
    p = Path(g.path)
    return p if p.is_file() else None
