"""Per-partner learned intent: does this message ask for work?

The regex layer (react_policy) is the floor and the teacher. This module
learns — locally, from this partner's own turns, no API calls — where the
regexes under-arm. v1 is deliberately one-directional: once confident, the
learner may ARM a message the regexes called non-work; it never disarms a
work verdict, so nothing the regexes grant is ever taken away.

Signals
-------
- teacher: every step-0 classification trains lightly toward the regex verdict
- confirmed work: a turn that executed >= 1 tool trains strongly toward work
- declined twice: the armed-ceiling cap passing with zero tools trains toward
  not-work (the model was offered tools twice and answered in words)

Model: hashed n-gram logistic regression (stable zlib.crc32 hashing, online
SGD, stdlib only). Weights persist under ``<home>/intent/model.json``.
Kill switch: ``REMEDY_INTENT_LEARN=0``.
"""

from __future__ import annotations

import json
import math
import os
import threading
import zlib
from contextlib import suppress
from pathlib import Path
from typing import Any

_BUCKETS = 1 << 15
_LR = 0.05
_TEACHER_WEIGHT = 0.2
_OUTCOME_WEIGHT = 1.0
_MAX_WEIGHTS = 20_000
_SAVE_EVERY = 20
_MAX_TEXT = 800

# Arm-only override gate: enough *outcome* evidence and a confident score.
_MIN_OUTCOMES = 50
_ARM_THRESHOLD = 0.85

_mutex = threading.Lock()
_models: dict[str, _Model] = {}


def _enabled() -> bool:
    return (os.environ.get("REMEDY_INTENT_LEARN") or "").strip() != "0"


def _home(home: str | Path | None) -> Path:
    if home:
        return Path(home).expanduser()
    try:
        from remedy.core.security import get_home_dir

        return get_home_dir()
    except Exception:
        env = (os.environ.get("REMEDY_HOME") or "").strip()
        return Path(env or "~/.remedy").expanduser()


def _features(text: str) -> list[int]:
    msg = " " + (text or "").strip().lower()[:_MAX_TEXT] + " "
    feats: set[int] = set()
    words = [w for w in msg.split() if w]
    for w in words:
        feats.add(zlib.crc32(("w:" + w).encode("utf-8")) % _BUCKETS)
    for a, b in zip(words, words[1:], strict=False):
        feats.add(zlib.crc32(("b:" + a + " " + b).encode("utf-8")) % _BUCKETS)
    for i in range(len(msg) - 2):
        feats.add(zlib.crc32(("c:" + msg[i : i + 3]).encode("utf-8")) % _BUCKETS)
    return list(feats)


class _Model:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.weights: dict[int, float] = {}
        self.bias = 0.0
        self.counts: dict[str, int] = {"teacher": 0, "work": 0, "declined": 0}
        self.dirty = 0
        with suppress(Exception):
            raw = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                self.weights = {
                    int(k): float(v) for k, v in (raw.get("weights") or {}).items()
                }
                self.bias = float(raw.get("bias") or 0.0)
                counts = raw.get("counts") or {}
                if isinstance(counts, dict):
                    for k in self.counts:
                        self.counts[k] = int(counts.get(k) or 0)

    def predict(self, feats: list[int]) -> float:
        z = self.bias + sum(self.weights.get(f, 0.0) for f in feats)
        z = max(-30.0, min(30.0, z))
        return 1.0 / (1.0 + math.exp(-z))

    def update(self, feats: list[int], label: float, weight: float) -> None:
        p = self.predict(feats)
        grad = (label - p) * _LR * weight
        self.bias += grad
        for f in feats:
            self.weights[f] = self.weights.get(f, 0.0) + grad
        if len(self.weights) > _MAX_WEIGHTS:
            keep = sorted(self.weights.items(), key=lambda kv: abs(kv[1]), reverse=True)
            self.weights = dict(keep[: _MAX_WEIGHTS // 2])
        self.dirty += 1
        if self.dirty >= _SAVE_EVERY:
            self.save()

    def save(self) -> None:
        self.dirty = 0
        with suppress(Exception):
            from remedy.core.atomic_json import write_json_atomic

            write_json_atomic(
                self.path,
                {
                    "weights": {str(k): round(v, 6) for k, v in self.weights.items()},
                    "bias": round(self.bias, 6),
                    "counts": self.counts,
                },
                indent=None,
            )

    def outcome_samples(self) -> int:
        return int(self.counts.get("work", 0)) + int(self.counts.get("declined", 0))


def _model(home: str | Path | None) -> _Model:
    base = _home(home)
    key = str(base)
    with _mutex:
        m = _models.get(key)
        if m is None:
            m = _Model(base / "intent" / "model.json")
            m.path.parent.mkdir(parents=True, exist_ok=True)
            _models[key] = m
        return m


def consult(
    message: str, *, regex_verdict: bool, home: str | Path | None = None
) -> bool:
    """Teach toward the regex verdict; arm when confidently smarter.

    Never disarms: a True regex verdict always stands. A False verdict is
    overridden to True only with enough outcome evidence and a confident
    score — cold or uncertain, the regexes rule.
    """
    if not _enabled() or not (message or "").strip():
        return bool(regex_verdict)
    verdict = bool(regex_verdict)
    with suppress(Exception):
        m = _model(home)
        feats = _features(message)
        with _mutex:
            p = m.predict(feats)
            m.counts["teacher"] += 1
            m.update(feats, 1.0 if verdict else 0.0, _TEACHER_WEIGHT)
        if (
            not verdict
            and m.outcome_samples() >= _MIN_OUTCOMES
            and p >= _ARM_THRESHOLD
        ):
            import logging

            logging.getLogger(__name__).info(
                "intent_learn arm override p=%.2f outcomes=%d", p, m.outcome_samples()
            )
            return True
    return verdict


def record_confirmed_work(message: str, home: str | Path | None = None) -> None:
    """A turn for this message really executed tools — strong work label."""
    if not _enabled() or not (message or "").strip():
        return
    with suppress(Exception):
        m = _model(home)
        with _mutex:
            m.counts["work"] += 1
            m.update(_features(message), 1.0, _OUTCOME_WEIGHT)


def record_tools_declined(message: str, home: str | Path | None = None) -> None:
    """Offered tools twice, answered in words — strong not-work label."""
    if not _enabled() or not (message or "").strip():
        return
    with suppress(Exception):
        m = _model(home)
        with _mutex:
            m.counts["declined"] += 1
            m.update(_features(message), 0.0, _OUTCOME_WEIGHT)


def snapshot(home: str | Path | None = None) -> dict[str, Any]:
    """Counts + size for status surfaces / tests."""
    m = _model(home)
    with _mutex:
        return {
            "enabled": _enabled(),
            "counts": dict(m.counts),
            "n_weights": len(m.weights),
            "outcome_samples": m.outcome_samples(),
        }
