"""NanoToken — multiprovider token accounting for the Remedy nanoswarm.

Remedy-owned byte-level BPE (when pack present) + class-weighted heuristic
fallback + per-provider|model calibration from real usage blobs.

No third-party tokenizer libraries or foreign merge tables.

Used by Memory Harness thresholds, cost tickers, and mid-session provider switch
recompute.
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Any

from remedy.nanoswarm.bpe_engine import (
    DEFAULT_PACK_ID,
    get_pack,
    list_available_packs,
)
from remedy.nanoswarm.bpe_engine import (
    count_tokens as bpe_count_tokens,
)
from remedy.nanoswarm.token_tables import (
    content_boost,
    list_families,
    msg_overhead,
    resolved_weights,
)

# Routing labels (not foreign tokenizer names we load)
_PROVIDER_FAMILY: dict[str, str] = {
    "openai": "openai-compat",
    "xai": "openai-compat",
    "groq": "openai-compat",
    "openrouter": "openai-compat",
    "mistral": "openai-compat",
    "google": "gemini-like",
    "anthropic": "anthropic-like",
    "deepseek": "deepseek-like",
    "ollama": "local",
    "demo": "local",
    "custom": "openai-compat",
}

# All providers currently share the single Remedy pack; family only changes
# overhead/heuristic scales. Future packs can diverge here.
_FAMILY_DEFAULT_PACK: dict[str, str] = {
    "openai-compat": DEFAULT_PACK_ID,
    "anthropic-like": DEFAULT_PACK_ID,
    "deepseek-like": DEFAULT_PACK_ID,
    "gemini-like": DEFAULT_PACK_ID,
    "local": DEFAULT_PACK_ID,
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
    """Return Remedy routing family label for provider/model (not a foreign vocab id)."""
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()
    fam = _PROVIDER_FAMILY.get(p, "openai-compat")
    if "claude" in m:
        fam = "anthropic-like"
    elif "deepseek" in m:
        fam = "deepseek-like"
    elif "gemini" in m:
        fam = "gemini-like"
    elif any(x in m for x in ("llama", "mistral", "codestral", "ollama", "phi-", "qwen")):
        fam = "local"
    return fam


def resolve_bpe_assignment(
    provider: str | None = None,
    model: str | None = None,
) -> dict[str, Any]:
    """Swarm assignment: which Remedy BPE pack applies to this provider/model.

    Only Remedy pack ids are returned — never third-party tokenizer names as deps.
    """
    fam = encoding_family(provider, model)
    # Config override: REMEDY_BPE_PACK or token maps later
    env_pack = (os.environ.get("REMEDY_BPE_PACK") or "").strip()
    pack_id = env_pack or _FAMILY_DEFAULT_PACK.get(fam) or DEFAULT_PACK_ID
    pack = get_pack(pack_id)
    if pack is None and pack_id != DEFAULT_PACK_ID:
        pack = get_pack(DEFAULT_PACK_ID)
        pack_id = DEFAULT_PACK_ID if pack else pack_id
    window = resolve_context_window(provider, model)
    if pack is not None:
        return {
            "family": fam,
            "bpe_pack_id": pack.id,
            "pack_version": pack.version,
            "method": "bpe",
            "context_window": window,
            "msg_overhead": pack.msg_overhead,
            "available": True,
        }
    return {
        "family": fam,
        "bpe_pack_id": None,
        "pack_version": None,
        "method": "heuristic",
        "context_window": window,
        "msg_overhead": msg_overhead(fam if fam in ("local",) else "cl100k"),
        "available": False,
    }


def _heuristic_family_key(family: str) -> str:
    """Map routing labels onto token_tables family pack keys."""
    return {
        "openai-compat": "cl100k",
        "anthropic-like": "anthropic",
        "deepseek-like": "deepseek",
        "gemini-like": "gemini",
        "local": "local",
    }.get(family, "cl100k")


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
    return resolved_weights(family)


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
    prefer_bpe: bool = True,
) -> int:
    """Token estimate: Remedy BPE when pack available, else class-weighted heuristic."""
    if not text:
        return 0
    fam = family or encoding_family(provider, model)
    if prefer_bpe:
        asg = resolve_bpe_assignment(provider, model)
        pack = get_pack(asg.get("bpe_pack_id")) if asg.get("available") else None
        if pack is not None:
            n = len(text)
            if n <= _FULL_SCAN_MAX:
                return max(0, bpe_count_tokens(text, pack))
            # Sample head+tail for huge blobs (snappiness)
            head = text[:_SAMPLE_HEAD]
            tail = text[-_SAMPLE_TAIL:]
            sample_n = bpe_count_tokens(head + tail, pack)
            sample_len = len(head) + len(tail) or 1
            return max(1, int(sample_n * (n / sample_len) + 0.5))

    hf = _heuristic_family_key(fam)
    weights = _weights_for_family(hf)
    boost = content_boost(hf, text)
    n = len(text)
    if n <= _FULL_SCAN_MAX:
        total_w = sum(_class_weight(ch, weights) for ch in text) * boost
        return max(0, int(total_w + 0.5))
    head = text[:_SAMPLE_HEAD]
    tail = text[-_SAMPLE_TAIL:]
    sample = head + tail
    sample_w = sum(_class_weight(ch, weights) for ch in sample)
    sample_len = len(sample) or 1
    total_w = sample_w * (n / sample_len) * boost
    return max(0, int(total_w + 0.5))


# Simple LRU-ish cache for message-list estimates (switch/re-render snappiness)
_msg_cache: dict[str, int] = {}
_msg_cache_order: list[str] = []
_MSG_CACHE_MAX = 64
_msg_cache_lock = threading.Lock()


def _messages_cache_key(
    messages: list[dict[str, Any]],
    provider: str | None,
    model: str | None,
    pack_id: str | None,
) -> str:
    # Cheap fingerprint: pack + roles + content lengths + tail hash
    parts: list[str] = [
        encoding_family(provider, model),
        pack_id or "heuristic",
        str(len(messages)),
    ]
    for m in messages[-12:]:
        c = m.get("content")
        clen = len(c) if isinstance(c, str) else len(str(c or ""))
        parts.append(f"{m.get('role')}:{clen}")
        tcs = m.get("tool_calls")
        if tcs:
            parts.append(f"tc{len(tcs)}")
    if messages:
        tail = messages[-1].get("content")
        if isinstance(tail, str) and tail:
            parts.append(tail[-80:])
    return "|".join(parts)


def estimate_messages_tokens(
    messages: list[dict[str, Any]] | None,
    *,
    provider: str | None = None,
    model: str | None = None,
) -> int:
    """Estimate tokens for a chat message list (content + light overhead)."""
    if not messages:
        return 0
    asg = resolve_bpe_assignment(provider, model)
    fam = str(asg.get("family") or encoding_family(provider, model))
    pack_id = asg.get("bpe_pack_id")
    key = _messages_cache_key(messages, provider, model, str(pack_id) if pack_id else None)
    with _msg_cache_lock:
        if key in _msg_cache:
            return _msg_cache[key]

    overhead = int(asg.get("msg_overhead") or msg_overhead(_heuristic_family_key(fam)))
    total = 0
    for m in messages:
        total += overhead
        content = m.get("content")
        if isinstance(content, str):
            total += estimate_text_tokens(
                content, provider=provider, model=model, family=fam
            )
        elif content is not None:
            try:
                total += estimate_text_tokens(
                    json.dumps(content, default=str),
                    provider=provider,
                    model=model,
                    family=fam,
                )
            except Exception:
                total += estimate_text_tokens(
                    str(content), provider=provider, model=model, family=fam
                )
        tcs = m.get("tool_calls")
        if tcs:
            try:
                total += estimate_text_tokens(
                    json.dumps(tcs, default=str),
                    provider=provider,
                    model=model,
                    family=fam,
                )
            except Exception:
                total += 32
        for k in ("reasoning_content", "thinking", "reasoning"):
            rc = m.get(k)
            if isinstance(rc, str) and rc:
                total += estimate_text_tokens(
                    rc, provider=provider, model=model, family=fam
                )
    result = max(1, total)
    with _msg_cache_lock:
        _msg_cache[key] = result
        _msg_cache_order.append(key)
        while len(_msg_cache_order) > _MSG_CACHE_MAX:
            old = _msg_cache_order.pop(0)
            _msg_cache.pop(old, None)
    return result


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
    bpe_pack_id: str | None = None
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
        self.last_assignment: dict[str, Any] = {}
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
        asg = resolve_bpe_assignment(provider, model)
        self.last_assignment = asg
        base_method = str(asg.get("method") or "heuristic")
        raw = estimate_messages_tokens(messages, provider=provider, model=model)
        if calibrate:
            adj, cal_method = self.calibrator.adjust(
                raw, provider=provider, model=model
            )
            method = (
                f"{base_method}+calibrated"
                if cal_method == "calibrated"
                else base_method
            )
        else:
            adj, method = raw, base_method
        self.last_method = method
        self.last_estimate = adj
        key = self._bucket_key(provider, model)
        fam = str(asg.get("family") or encoding_family(provider, model))
        window = int(asg.get("context_window") or resolve_context_window(provider, model))
        with self._lock:
            b = self._buckets.get(key)
            if b is None:
                b = TokenBucketCache(
                    key=key,
                    provider=(provider or "").lower(),
                    model=(model or "").lower(),
                    family=fam,
                    context_window=window,
                    bpe_pack_id=asg.get("bpe_pack_id"),
                )
                self._buckets[key] = b
            b.last_estimate = adj
            b.last_method = method
            b.context_window = window
            b.family = fam
            b.bpe_pack_id = asg.get("bpe_pack_id")
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
        """Assign Remedy BPE pack for provider/model and remeasure history."""
        prov = (provider or "").strip().lower()
        mod = (model or "").strip()
        self.active_provider = prov or None
        self.active_model = mod or None
        asg = resolve_bpe_assignment(prov, mod)
        self.last_assignment = asg
        window = int(asg.get("context_window") or resolve_context_window(prov, mod))
        fam = str(asg.get("family") or encoding_family(prov, mod))
        est = 0
        method = str(asg.get("method") or "heuristic")
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
            "bpe_pack_id": asg.get("bpe_pack_id"),
            "pack_version": asg.get("pack_version"),
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
                b.bpe_pack_id = asg.get("bpe_pack_id")
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
                    "bpe_pack_id": v.bpe_pack_id,
                    "context_window": v.context_window,
                    "last_estimate": v.last_estimate,
                    "last_method": v.last_method,
                    "last_fill_pct": round(v.last_fill_pct, 4),
                    "last_remeasure_at": v.last_remeasure_at,
                }
                for k, v in self._buckets.items()
            }
        asg = self.last_assignment or resolve_bpe_assignment(
            self.active_provider, self.active_model
        )
        return {
            "bot": "token",
            "label": "NanoToken",
            "last_method": self.last_method,
            "last_estimate": self.last_estimate,
            "active_provider": self.active_provider,
            "active_model": self.active_model,
            "bpe_assignment": asg,
            "bpe_packs": list_available_packs(),
            "calibrator": self.calibrator.stats(),
            "buckets": buckets,
            "families": list_families(),
            "ip_note": (
                "Remedy-owned BBPE packs only; no third-party tokenizer "
                "libraries or foreign merge tables."
            ),
        }


_default_token: TokenNanobot | None = None
_token_lock = threading.Lock()


def get_token_nanobot() -> TokenNanobot:
    global _default_token
    with _token_lock:
        if _default_token is None:
            _default_token = TokenNanobot()
        return _default_token
