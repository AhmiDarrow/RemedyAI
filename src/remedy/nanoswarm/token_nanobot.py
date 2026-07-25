"""In-house token measurement — no tiktoken / Gigatoken / third-party tokenizers.

Class-weighted character estimate + optional usage calibration from provider
`usage` blobs. Used by Memory Harness thresholds and cost ticker fallbacks.
"""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from typing import Any

# Weights: effective chars per "token-ish" unit (lower weight → more tokens).
# Tuned as constants; calibrator applies linear correction per provider/model.
_WEIGHTS = {
    "ascii_word": 0.25,  # ~4 chars/token baseline
    "space": 0.15,
    "code_punct": 0.45,  # denser than prose
    "digit": 0.30,
    "cjk": 1.0,  # often ~1 token per char
    "emoji": 0.8,
    "other": 0.35,
}

_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\uff66-\uff9f\uac00-\ud7af]"
)
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F1E0-\U0001F1FF]"
)


def _class_weight(ch: str) -> float:
    if ch.isspace():
        return _WEIGHTS["space"]
    if _EMOJI_RE.match(ch):
        return _WEIGHTS["emoji"]
    if _CJK_RE.match(ch):
        return _WEIGHTS["cjk"]
    if ch.isalnum() and ord(ch) < 128:
        if ch.isdigit():
            return _WEIGHTS["digit"]
        return _WEIGHTS["ascii_word"]
    if ord(ch) < 128:
        return _WEIGHTS["code_punct"]
    return _WEIGHTS["other"]


def estimate_text_tokens(text: str | None) -> int:
    """In-house class-weighted estimate for a single string."""
    if not text:
        return 0
    total_w = 0.0
    for ch in text:
        total_w += _class_weight(ch)
    # weight is "token density factor"; sum of weights ≈ token count
    return max(0, int(total_w + 0.5))


def estimate_messages_tokens(messages: list[dict[str, Any]] | None) -> int:
    """Estimate tokens for a chat message list (content + light overhead)."""
    if not messages:
        return 0
    total = 0
    for m in messages:
        # role overhead ~4 tokens
        total += 4
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_text_tokens(content)
        elif content is not None:
            try:
                total += estimate_text_tokens(json.dumps(content, default=str))
            except Exception:
                total += estimate_text_tokens(str(content))
        # tool calls
        tcs = m.get("tool_calls")
        if tcs:
            try:
                total += estimate_text_tokens(json.dumps(tcs, default=str))
            except Exception:
                total += 32
    return max(1, total)


@dataclass
class _CalibSample:
    estimated: float
    actual: float


@dataclass
class UsageCalibrator:
    """Per provider/model linear fit: actual ≈ slope * est + intercept."""

    max_samples: int = 64
    min_samples: int = 4
    _data: dict[str, list[_CalibSample]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def _key(self, provider: str | None, model: str | None) -> str:
        return f"{(provider or '').lower()}|{(model or '').lower()}"

    def observe(
        self,
        estimated: int,
        actual: int,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        if estimated <= 0 or actual <= 0:
            return
        key = self._key(provider, model)
        with self._lock:
            buf = self._data.setdefault(key, [])
            buf.append(_CalibSample(float(estimated), float(actual)))
            if len(buf) > self.max_samples:
                del buf[: len(buf) - self.max_samples]

    def adjust(
        self,
        estimated: int,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> tuple[int, str]:
        """Return (adjusted, method) where method is heuristic|calibrated."""
        if estimated <= 0:
            return 0, "heuristic"
        key = self._key(provider, model)
        with self._lock:
            buf = list(self._data.get(key) or [])
        if len(buf) < self.min_samples:
            return estimated, "heuristic"
        # Simple slope through origin + mean bias blend
        sum_e = sum(s.estimated for s in buf)
        sum_a = sum(s.actual for s in buf)
        if sum_e <= 0:
            return estimated, "heuristic"
        slope = sum_a / sum_e
        # Clamp slope so one bad sample cannot explode estimates
        slope = max(0.5, min(2.0, slope))
        adj = int(estimated * slope + 0.5)
        return max(1, adj), "calibrated"

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                k: {"n": len(v), "last_ratio": (v[-1].actual / v[-1].estimated if v and v[-1].estimated else None)}
                for k, v in self._data.items()
            }


_default_calibrator = UsageCalibrator()


class TokenNanobot:
    """Facade for harness + usage: measure, calibrate, budget."""

    def __init__(self, calibrator: UsageCalibrator | None = None) -> None:
        self.calibrator = calibrator or _default_calibrator
        self.last_method: str = "heuristic"
        self.last_estimate: int = 0

    def measure_text(self, text: str | None) -> int:
        return estimate_text_tokens(text)

    def measure_messages(
        self,
        messages: list[dict[str, Any]] | None,
        *,
        provider: str | None = None,
        model: str | None = None,
        calibrate: bool = True,
    ) -> int:
        raw = estimate_messages_tokens(messages)
        if calibrate:
            adj, method = self.calibrator.adjust(raw, provider=provider, model=model)
        else:
            adj, method = raw, "heuristic"
        self.last_method = method
        self.last_estimate = adj
        return adj

    def observe_usage(
        self,
        estimated: int,
        actual: int,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> None:
        self.calibrator.observe(estimated, actual, provider=provider, model=model)

    def should_nudge_compress(
        self,
        token_estimate: int,
        *,
        context_window: int = 200_000,
        min_pct: float = 0.75,
        max_pct: float = 0.92,
        brief_tokens: int = 0,
    ) -> str | None:
        if context_window <= 0:
            return None
        total = token_estimate + max(0, brief_tokens)
        pct = total / context_window
        if pct >= max_pct:
            return "strong"
        if pct >= min_pct:
            return "soft"
        return None

    def fill_pct(
        self,
        token_estimate: int,
        *,
        context_window: int = 200_000,
        brief_tokens: int = 0,
    ) -> float:
        if context_window <= 0:
            return 0.0
        return min(1.0, (token_estimate + max(0, brief_tokens)) / context_window)

    def status(self) -> dict[str, Any]:
        return {
            "bot": "token",
            "last_method": self.last_method,
            "last_estimate": self.last_estimate,
            "calibrator": self.calibrator.stats(),
        }


_default_token: TokenNanobot | None = None
_token_lock = threading.Lock()


def get_token_nanobot() -> TokenNanobot:
    global _default_token
    with _token_lock:
        if _default_token is None:
            _default_token = TokenNanobot()
        return _default_token
