"""NanoToken — multiprovider token accounting for the Remedy nanoswarm.

In-house class-weighted estimates + per-provider|model calibration from real
usage blobs. No tiktoken / Gigatoken dependency; accuracy converges via
calibration and encoding-family weight packs.

Used by Memory Harness thresholds, cost tickers, and mid-session provider switch
recompute.
"""

from __future__ import annotations

import json
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

# Baseline weights: effective "token density" per char class.
# Lower weight → fewer tokens counted per character (~4 chars/token for ascii_word).
_WEIGHTS_DEFAULT = {
    "ascii_word": 0.25,
    "space": 0.15,
    "code_punct": 0.45,
    "digit": 0.30,
    "cjk": 1.0,
    "emoji": 0.8,
    "other": 0.35,
}

# Encoding families (tiktoken/Gigatoken-class *heuristics*, not full BPE).
# Scale factors multiply the default class weights to better match family tokenizers.
_FAMILY_SCALES: dict[str, dict[str, float]] = {
    # OpenAI cl100k / o200k-ish + many OpenAI-compat APIs (xAI, Groq, OpenRouter OpenAI models)
    "cl100k": {
        "ascii_word": 1.0,
        "space": 1.0,
        "code_punct": 1.05,
        "digit": 1.0,
        "cjk": 0.95,
        "emoji": 1.1,
        "other": 1.0,
    },
    # Anthropic-ish (slightly denser punctuation / markup)
    "anthropic": {
        "ascii_word": 1.02,
        "space": 1.0,
        "code_punct": 1.12,
        "digit": 1.0,
        "cjk": 1.0,
        "emoji": 1.05,
        "other": 1.05,
    },
    # DeepSeek / some code-heavy models
    "deepseek": {
        "ascii_word": 0.98,
        "space": 0.95,
        "code_punct": 1.15,
        "digit": 1.0,
        "cjk": 1.05,
        "emoji": 1.0,
        "other": 1.0,
    },
    # Local / demo — keep neutral
    "local": {
        "ascii_word": 1.0,
        "space": 1.0,
        "code_punct": 1.0,
        "digit": 1.0,
        "cjk": 1.0,
        "emoji": 1.0,
        "other": 1.0,
    },
}

_PROVIDER_FAMILY: dict[str, str] = {
    "openai": "cl100k",
    "xai": "cl100k",
    "groq": "cl100k",
    "openrouter": "cl100k",
    "mistral": "cl100k",
    "google": "cl100k",
    "anthropic": "anthropic",
    "deepseek": "deepseek",
    "ollama": "local",
    "demo": "local",
    "custom": "cl100k",
}

# Default context windows by model pattern (conservative).
_WINDOW_RULES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"gpt-4o|gpt-4\.1|o3|o4|o1", re.I), 128_000),
    (re.compile(r"gpt-3\.5", re.I), 16_384),
    (re.compile(r"claude.*(opus|sonnet|haiku)|claude-3|claude-4", re.I), 200_000),
    (re.compile(r"grok-4|grok-3", re.I), 131_072),
    (re.compile(r"deepseek", re.I), 128_000),
    (re.compile(r"gemini", re.I), 128_000),
    (re.compile(r"llama|mixtral|mistral|codestral", re.I), 32_768),
    (re.compile(r"demo|local|ollama", re.I), 32_768),
]
_DEFAULT_WINDOW = 128_000

_CJK_RE = re.compile(
    r"[\u3000-\u303f\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff"
    r"\uf900-\ufaff\uff66-\uff9f\uac00-\ud7af]"
)
_EMOJI_RE = re.compile(
    r"[\U0001F300-\U0001FAFF\U00002700-\U000027BF\U0001F1E0-\U0001F1FF]"
)

# Sample large strings instead of full O(n) walks (snappiness).
_FULL_SCAN_MAX = 24_000
_SAMPLE_HEAD = 8_000
_SAMPLE_TAIL = 8_000


def encoding_family(provider: str | None = None, model: str | None = None) -> str:
    """Return encoding family id for provider/model."""
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()
    if p in _PROVIDER_FAMILY:
        fam = _PROVIDER_FAMILY[p]
    else:
        fam = "cl100k"
    if "claude" in m:
        fam = "anthropic"
    elif "deepseek" in m:
        fam = "deepseek"
    elif any(x in m for x in ("llama", "mistral", "codestral", "ollama")):
        fam = "local"
    return fam


def resolve_context_window(
    provider: str | None = None,
    model: str | None = None,
    *,
    fallback: int = _DEFAULT_WINDOW,
) -> int:
    """Best-effort context window for fill% after provider switch."""
    blob = f"{provider or ''} {model or ''}".strip()
    if not blob:
        return fallback
    for pat, win in _WINDOW_RULES:
        if pat.search(blob):
            return win
    return fallback


def _weights_for_family(family: str) -> dict[str, float]:
    scales = _FAMILY_SCALES.get(family) or _FAMILY_SCALES["cl100k"]
    return {k: _WEIGHTS_DEFAULT[k] * float(scales.get(k, 1.0)) for k in _WEIGHTS_DEFAULT}


def _class_weight(ch: str, weights: dict[str, float]) -> float:
    if ch.isspace():
        return weights["space"]
    if _EMOJI_RE.match(ch):
        return weights["emoji"]
    if _CJK_RE.match(ch):
        return weights["cjk"]
    if ch.isalnum() and ord(ch) < 128:
        if ch.isdigit():
            return weights["digit"]
        return weights["ascii_word"]
    if ord(ch) < 128:
        return weights["code_punct"]
    return weights["other"]


def estimate_text_tokens(
    text: str | None,
    *,
    provider: str | None = None,
    model: str | None = None,
    family: str | None = None,
) -> int:
    """Class-weighted estimate; samples very large strings for snappiness."""
    if not text:
        return 0
    fam = family or encoding_family(provider, model)
    weights = _weights_for_family(fam)
    n = len(text)
    if n <= _FULL_SCAN_MAX:
        total_w = sum(_class_weight(ch, weights) for ch in text)
        return max(0, int(total_w + 0.5))
    # Sample head + tail; scale by full length
    head = text[:_SAMPLE_HEAD]
    tail = text[-_SAMPLE_TAIL:]
    sample = head + tail
    sample_w = sum(_class_weight(ch, weights) for ch in sample)
    sample_len = len(sample) or 1
    total_w = sample_w * (n / sample_len)
    return max(0, int(total_w + 0.5))


def estimate_messages_tokens(
    messages: list[dict[str, Any]] | None,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """Estimate tokens for a chat message list (content + light overhead)."""
    if not messages:
        return 0
    fam = encoding_family(provider, model)
    total = 0
    for m in messages:
        total += 4  # role overhead
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_text_tokens(content, family=fam)
        elif content is not None:
            try:
                total += estimate_text_tokens(
                    json.dumps(content, default=str), family=fam
                )
            except Exception:
                total += estimate_text_tokens(str(content), family=fam)
        tcs = m.get("tool_calls")
        if tcs:
            try:
                total += estimate_text_tokens(json.dumps(tcs, default=str), family=fam)
            except Exception:
                total += 32
    return max(1, total)


@dataclass
class _CalibSample:
    estimated: float
    actual: float


@dataclass
class UsageCalibrator:
    """Per provider/model linear fit: actual ≈ slope * est."""

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
        if estimated <= 0:
            return 0, "heuristic"
        key = self._key(provider, model)
        with self._lock:
            buf = list(self._data.get(key) or [])
        if len(buf) < self.min_samples:
            return estimated, "heuristic"
        sum_e = sum(s.estimated for s in buf)
        sum_a = sum(s.actual for s in buf)
        if sum_e <= 0:
            return estimated, "heuristic"
        slope = sum_a / sum_e
        slope = max(0.5, min(2.0, slope))
        adj = int(estimated * slope + 0.5)
        return max(1, adj), "calibrated"

    def stats(self) -> dict[str, Any]:
        with self._lock:
            return {
                k: {
                    "n": len(v),
                    "last_ratio": (
                        (v[-1].actual / v[-1].estimated)
                        if v and v[-1].estimated
                        else None
                    ),
                }
                for k, v in self._data.items()
            }


_default_calibrator = UsageCalibrator()


@dataclass
class TokenBucketCache:
    """Hot cache for a provider|model estimate after switch / measure."""

    key: str
    provider: str
    model: str
    family: str
    context_window: int
    last_estimate: int = 0
    last_method: str = "heuristic"
    last_fill_pct: float = 0.0
    last_remeasure_at: float = 0.0
    last_nudge: str | None = None


class TokenNanobot:
    """NanoToken facade: multiprovider measure, calibrate, remeasure, budget."""

    def __init__(self, calibrator: UsageCalibrator | None = None) -> None:
        self.calibrator = calibrator or _default_calibrator
        self.last_method: str = "heuristic"
        self.last_estimate: int = 0
        self.active_provider: str | None = None
        self.active_model: str | None = None
        self._buckets: dict[str, TokenBucketCache] = {}
        self._session_remeasure: dict[str, dict[str, Any]] = {}
        self._lock = threading.Lock()

    def _bucket_key(self, provider: str | None, model: str | None) -> str:
        return f"{(provider or '').lower()}|{(model or '').lower()}"

    def measure_text(
        self,
        text: str | None,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> int:
        return estimate_text_tokens(text, provider=provider, model=model)

    def measure_messages(
        self,
        messages: list[dict[str, Any]] | None,
        *,
        provider: str | None = None,
        model: str | None = None,
        calibrate: bool = True,
    ) -> int:
        raw = estimate_messages_tokens(messages, provider=provider, model=model)
        if calibrate:
            adj, method = self.calibrator.adjust(raw, provider=provider, model=model)
        else:
            adj, method = raw, "heuristic"
        self.last_method = method
        self.last_estimate = adj
        key = self._bucket_key(provider, model)
        fam = encoding_family(provider, model)
        window = resolve_context_window(provider, model)
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = TokenBucketCache(
                    key=key,
                    provider=(provider or "").lower(),
                    model=(model or "").lower(),
                    family=fam,
                    context_window=window,
                )
                self._buckets[key] = b
            b.last_estimate = adj
            b.last_method = method
            b.context_window = window
            b.family = fam
            b.last_fill_pct = self.fill_pct(adj, context_window=window)
            b.last_remeasure_at = time.time()
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

    def on_provider_changed(
        self,
        provider: str,
        model: str | None = None,
        *,
        session_id: str | None = None,
        messages: list[dict[str, Any]] | None = None,
        old_provider: str | None = None,
        old_model: str | None = None,
        min_pct: float = 0.75,
        max_pct: float = 0.92,
    ) -> dict[str, Any]:
        """Select cache bucket and remeasure history under the new provider/model."""
        prov = (provider or "").strip().lower()
        mod = (model or "").strip()
        self.active_provider = prov or None
        self.active_model = mod or None
        window = resolve_context_window(prov, mod)
        fam = encoding_family(prov, mod)
        est = 0
        method = "heuristic"
        if messages:
            est = self.measure_messages(messages, provider=prov, model=mod)
            method = self.last_method
        fill = self.fill_pct(est, context_window=window)
        nudge = self.should_nudge_compress(
            est, context_window=window, min_pct=min_pct, max_pct=max_pct
        )
        out: dict[str, Any] = {
            "bot": "token",
            "provider": prov,
            "model": mod,
            "old_provider": old_provider,
            "old_model": old_model,
            "encoding_family": fam,
            "context_window": window,
            "token_estimate": est,
            "fill_pct": round(fill, 4),
            "nudge": nudge,
            "estimate_method": method,
            "remeasured": bool(messages),
            "session_id": (session_id or "").strip() or None,
        }
        sid = (session_id or "").strip() or "_default"
        with self._lock:
            self._session_remeasure[sid] = out
            key = self._bucket_key(prov, mod)
            b = self._buckets.get(key)
            if b is not None:
                b.last_nudge = nudge
                b.last_fill_pct = fill
        return out

    def last_remeasure(self, session_id: str | None = None) -> dict[str, Any] | None:
        sid = (session_id or "").strip() or "_default"
        with self._lock:
            return dict(self._session_remeasure.get(sid) or {}) or None

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
        with self._lock:
            buckets = {
                k: {
                    "family": v.family,
                    "context_window": v.context_window,
                    "last_estimate": v.last_estimate,
                    "last_method": v.last_method,
                    "last_fill_pct": round(v.last_fill_pct, 4),
                    "last_remeasure_at": v.last_remeasure_at,
                }
                for k, v in self._buckets.items()
            }
        return {
            "bot": "token",
            "label": "NanoToken",
            "last_method": self.last_method,
            "last_estimate": self.last_estimate,
            "active_provider": self.active_provider,
            "active_model": self.active_model,
            "calibrator": self.calibrator.stats(),
            "buckets": buckets,
        }


_default_token: TokenNanobot | None = None
_token_lock = threading.Lock()


def get_token_nanobot() -> TokenNanobot:
    global _default_token
    with _token_lock:
        if _default_token is None:
            _default_token = TokenNanobot()
        return _default_token
