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

# Default context windows by model pattern (conservative). Cloud providers only —
# local models (ollama / llama.cpp) are resolved separately by size suffix, since
# a small local n_ctx (often 4k–8k) is the whole point of the budget.
_WINDOW_RULES: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"gpt-4o|gpt-4\.1|o3|o4|o1", re.I), 128_000),
    (re.compile(r"gpt-3\.5", re.I), 16_384),
    (re.compile(r"claude.*(opus|sonnet|haiku)|claude-3|claude-4", re.I), 200_000),
    (re.compile(r"grok-4|grok-3", re.I), 131_072),
    (re.compile(r"gemini", re.I), 128_000),
    (re.compile(r"llama|mixtral|mistral|codestral", re.I), 128_000),
]
# Cloud models whose local family would otherwise look tiny; the big-window
# family rules above still apply to them (e.g. Groq llama-3.3-70b → 128k).
_DEFAULT_WINDOW = 128_000

# Conservative n_ctx for a local model with no size hint. Most small models
# default to 4k–8k; a guess of 32k is far too optimistic and floods a 4k model.
_LOCAL_DEFAULT_WINDOW = 8_192

# Size suffix ("qwen2.5:7b-instruct", "llama3.2:3b") → conservative n_ctx.
# A ~8B local model commonly runs 8k–32k; 1B–3B typically 4k–8k. Budgeting low
# is safe — the harness compresses earlier instead of overfilling the window.
_LOCAL_SIZE_WINDOWS: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r":\s*(?:0?\.5|1(?!\d))\s*b(?!\w)", re.I), 4_096),  # 0.5b / 1b
    (re.compile(r":\s*(?:2|3|2\.5)\s*b(?!\w)", re.I), 8_192),      # 2b / 2.5b / 3b
    (re.compile(r":\s*[4-9]\s*b(?!\w)", re.I), 16_384),            # 4b–9b
    (re.compile(r":\s*1[0-9]\s*b(?!\w)", re.I), 32_768),           # 10b–19b
]

# Providers that are definitively local (their model runs on-device).
_LOCAL_PROVIDERS = frozenset({"ollama", "demo", "local", "llamacpp", "rmb", "llama"})

# Cloud providers that may still serve a local-family model name (e.g. Groq).
# ``custom`` is *not* always cloud — loopback + .rmb4 models are local (below).
_CLOUD_PROVIDERS = frozenset(
    {"openai", "anthropic", "google", "deepseek", "xai", "groq", "mistral",
     "openrouter", "poe"}
)

# Live discovery cache: key → (monotonic_ts, window). Filled from GET /v1/models
# (RMB advertises context_window) or env override. Avoids budgeting at 128k
# against a 4–6k physical host window.
_WINDOW_CACHE: dict[str, tuple[float, int]] = {}
_WINDOW_CACHE_TTL_S = 300.0
_window_cache_lock = threading.Lock()

# Also match underscore / bare size tags used by RMB lattices and HF ids:
#   qwen25_coder_7b.rmb4, Qwen2.5-7B-Instruct, llama-3.2-3b
_LOCAL_SIZE_WINDOWS_EXTRA: list[tuple[re.Pattern[str], int]] = [
    (re.compile(r"(?:^|[^0-9])(?:0?\.5|1(?!\d))\s*b(?:\b|[_-]|\.)", re.I), 4_096),
    (re.compile(r"(?:^|[^0-9])(?:2|2\.5|3)\s*b(?:\b|[_-]|\.)", re.I), 8_192),
    (re.compile(r"(?:^|[^0-9])[4-9]\s*b(?:\b|[_-]|\.)", re.I), 16_384),
    (re.compile(r"(?:^|[^0-9])1[0-9]\s*b(?:\b|[_-]|\.)", re.I), 32_768),
]


def _cache_key(base_url: str | None, model: str | None) -> str:
    return f"{(base_url or '').rstrip('/').lower()}|{(model or '').strip().lower()}"


def cache_context_window(
    base_url: str | None,
    model: str | None,
    window: int,
    *,
    ttl_s: float = _WINDOW_CACHE_TTL_S,
) -> None:
    """Store a discovered n_ctx (e.g. from RMB GET /v1/models)."""
    try:
        w = int(window)
    except (TypeError, ValueError):
        return
    if w < 512:
        return
    w = min(w, 1_000_000)
    key = _cache_key(base_url, model)
    with _window_cache_lock:
        _WINDOW_CACHE[key] = (time.monotonic() + float(ttl_s), w)
        # Also cache by model alone for harness paths that lack base_url.
        if model:
            _WINDOW_CACHE[_cache_key("", model)] = (time.monotonic() + float(ttl_s), w)


def get_cached_context_window(
    base_url: str | None = None,
    model: str | None = None,
) -> int | None:
    """Return a non-expired discovered window, or None."""
    now = time.monotonic()
    with _window_cache_lock:
        for key in (_cache_key(base_url, model), _cache_key("", model)):
            hit = _WINDOW_CACHE.get(key)
            if not hit:
                continue
            exp, w = hit
            if exp >= now and w >= 512:
                return int(w)
    return None


def clear_context_window_cache() -> None:
    with _window_cache_lock:
        _WINDOW_CACHE.clear()


def is_local_model(
    provider: str | None = None,
    model: str | None = None,
    *,
    base_url: str | None = None,
) -> bool:
    """True when the model runs on-device (Ollama, llama.cpp, RMB, loopback)."""
    p = (provider or "").strip().lower()
    m = (model or "").strip().lower()
    url = (base_url or "").strip().lower()
    if p in _LOCAL_PROVIDERS:
        return True
    if m.endswith(".rmb4") or m.endswith(".mwi") or "rmb" in m:
        return True
    if ":8787" in url or url.rstrip("/").endswith(":8787") or "/rmb" in url:
        return True
    if _is_loopback_url(url):
        return True
    if _model_size_window(m) is not None and p in ("custom", "ollama", "llamacpp", "rmb", "local", ""):
        return True
    family = encoding_family(p, m)
    if family == "local" and p not in _CLOUD_PROVIDERS:
        return True
    return False


def _is_loopback_url(url: str) -> bool:
    if not url:
        return False
    try:
        from urllib.parse import urlsplit

        host = (urlsplit(url).hostname or "").lower()
    except Exception:
        host = ""
    return host in ("localhost", "0.0.0.0", "::1") or host.startswith("127.")


def _model_size_window(model: str | None) -> int | None:
    """n_ctx guess from a model's size suffix (e.g. 'qwen2.5:7b' → 16k)."""
    ml = (model or "").lower()
    for pat, win in _LOCAL_SIZE_WINDOWS:
        if pat.search(ml):
            return win
    for pat, win in _LOCAL_SIZE_WINDOWS_EXTRA:
        if pat.search(ml):
            return win
    return None

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
    base_url: str | None = None,
    fallback: int = _DEFAULT_WINDOW,
) -> int:
    """Best-effort context window for fill% after provider switch.

    Priority:
      1. ``REMEDY_CONTEXT_WINDOW`` env override
      2. Live cache from GET /v1/models (RMB advertises ``context_window``)
      3. Local heuristics (size suffix / .rmb4 / loopback) — never 128k
      4. Cloud model-name rules

    Local providers (ollama / llama.cpp / RMB) are budgeted against a
    conservative on-device window — never a cloud-scale guess. A model with a
    size suffix (``qwen2.5:7b``, ``qwen25_coder_7b.rmb4``) is treated as local
    even under a ``custom`` provider.
    """
    env_win = (os.environ.get("REMEDY_CONTEXT_WINDOW") or "").strip()
    if env_win.isdigit():
        return max(512, min(int(env_win), 1_000_000))

    cached = get_cached_context_window(base_url, model)
    if cached is not None:
        return cached

    p = (provider or "").strip().lower()
    m = (model or "").strip()
    ml = m.lower()
    local = is_local_model(p, m, base_url=base_url)

    # RMB managed host: configured ctx_size is the physical window (e.g. 32k).
    # Prefer it over size heuristics so harness + endless_context budget correctly.
    if local or p == "rmb":
        try:
            from remedy.runtime.rmb.mode import is_rmb_provider
            from remedy.runtime.rmb.config import load_rmb_json, merge_state

            if p == "rmb" or is_rmb_provider(p, base_url):
                st = merge_state(load_rmb_json())
                ctx = int(st.get("ctx_size") or 0)
                if ctx >= 2048:
                    url = str(st.get("base_url") or base_url or "")
                    cache_context_window(url, m or st.get("model_id"), ctx)
                    return ctx
        except Exception:
            pass

    if local:
        size = _model_size_window(m)
        if size is not None:
            # Legacy .rmb4 lattice files often ran 4–8k; keep conservative there.
            if ml.endswith(".rmb4") or ml.endswith(".mwi"):
                return min(size, 8_192)
            return size
        if ml.endswith(".rmb4") or ml.endswith(".mwi"):
            return 6_144  # typical RMB agent profile window after auto_ctx
        return _LOCAL_DEFAULT_WINDOW
    blob = f"{p} {ml}".strip()
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
