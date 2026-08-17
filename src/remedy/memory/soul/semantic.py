"""Semantic recall — the muscle that finds a memory by *meaning*, not words.

Every recall path in the soul so far is keyword overlap: "launch" only finds a
memory if the stored line literally contains "launch". A person doesn't remember
that way — ask about the launch and the memory of *shipping the rocket* surfaces,
no shared word required. This module adds that: an optional, entirely local
embedding index over the soul's recall candidates, brute-force cosine ranked
(the memory sets are tiny — a few dozen lines — so no faiss, no numpy, no heavy
index is warranted or wanted).

Design rules, matching the rest of the soul layer:
  * Local-first and OPTIONAL. If no embedder is reachable, every function here
    returns None and callers fall back to the existing keyword recall unchanged.
    It never raises, never blocks hard, never reaches a cloud model.
  * Cheap and cached. Content vectors are cached on disk keyed by text hash, so
    only new/changed lines (and the query) are ever embedded.
  * Read-only w.r.t. the soul field — like every recall path, it must not mutate
    the (possibly cached) field object.

Embedder resolution order: a test/override hook, then an OpenAI-compatible local
embeddings endpoint (llama.cpp / LM Studio / Ollama / RMB), then None.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import threading
import time
from collections.abc import Callable, Sequence
from contextlib import suppress
from pathlib import Path
from typing import Any

from remedy.memory.soul.field import soul_dir

CACHE_FILENAME = "embeddings.json"
MAX_CACHED_VECS = 600          # ring the cache so it can't grow without bound
_UNAVAIL_COOLDOWN_S = 90.0     # after a failed reach, don't retry for a while
_DEFAULT_TIMEOUT_S = 3.0

_lock = threading.Lock()
_unavailable_until: float = 0.0

# Test / integration hook: set to a callable ``list[str] -> list[list[float]]``
# to supply embeddings directly (bypasses the network). None disables it.
_EMBED_OVERRIDE: Callable[[list[str]], list[list[float]] | None] | None = None


def set_embed_override(fn: Callable[[list[str]], list[list[float]] | None] | None) -> None:
    """Install (or clear) a direct embedder — used by tests and local wiring."""
    global _EMBED_OVERRIDE, _unavailable_until
    _EMBED_OVERRIDE = fn
    _unavailable_until = 0.0  # a new embedder deserves a fresh attempt


def _embed_url() -> str:
    """OpenAI-compatible embeddings endpoint, if the owner configured a local one."""
    direct = (os.environ.get("REMEDY_EMBED_URL") or "").strip()
    if direct:
        return direct
    base = (
        os.environ.get("REMEDY_LOCAL_LLM_URL")
        or os.environ.get("REMEDY_RMB_URL")
        or ""
    ).strip()
    if not base:
        return ""
    base = base.rstrip("/")
    # Accept either a bare host (…/v1) or a full chat URL; normalize to embeddings.
    if base.endswith("/embeddings"):
        return base
    if base.endswith("/chat/completions"):
        base = base[: -len("/chat/completions")]
    if base.endswith("/v1"):
        return base + "/embeddings"
    return base + "/v1/embeddings"


def _embed_model() -> str:
    return (os.environ.get("REMEDY_EMBED_MODEL") or "nomic-embed-text").strip()


def _embed_remote(texts: list[str], *, timeout: float = _DEFAULT_TIMEOUT_S) -> list[list[float]] | None:
    url = _embed_url()
    if not url or not texts:
        return None
    import urllib.error
    import urllib.request

    payload = json.dumps({"input": texts, "model": _embed_model()}).encode("utf-8")
    req = urllib.request.Request(
        url, data=payload, headers={"Content-Type": "application/json"}, method="POST"
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", "replace")
        data = json.loads(raw)
    except (urllib.error.URLError, OSError, ValueError, TimeoutError):
        return None
    rows = data.get("data") if isinstance(data, dict) else None
    if not isinstance(rows, list) or not rows:
        return None
    out: list[list[float]] = []
    for r in rows:
        vec = r.get("embedding") if isinstance(r, dict) else None
        if not isinstance(vec, list) or not vec:
            return None
        with suppress(TypeError, ValueError):
            out.append([float(x) for x in vec])
    return out if len(out) == len(texts) else None


def embed_texts(texts: Sequence[str]) -> list[list[float]] | None:
    """Embed texts locally, or return None when no embedder is reachable.

    A per-process cooldown avoids hammering a dead endpoint on every recall.
    """
    global _unavailable_until
    items = [str(t or "") for t in texts]
    if not items:
        return []
    if _EMBED_OVERRIDE is not None:
        with suppress(Exception):
            res = _EMBED_OVERRIDE(items)
            if res and len(res) == len(items):
                return [list(map(float, v)) for v in res]
        return None
    if time.time() < _unavailable_until:
        return None
    res = _embed_remote(items)
    if res is None:
        _unavailable_until = time.time() + _UNAVAIL_COOLDOWN_S
    return res


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = 0.0
    na = 0.0
    nb = 0.0
    for x, y in zip(a, b, strict=False):
        dot += x * y
        na += x * x
        nb += y * y
    if na <= 0.0 or nb <= 0.0:
        return 0.0
    return dot / (math.sqrt(na) * math.sqrt(nb))


def _cache_path(home: str | Path | None) -> Path:
    return soul_dir(home) / CACHE_FILENAME


def _hash(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:16]


def _load_cache(home: str | Path | None) -> dict[str, Any]:
    p = _cache_path(home)
    with suppress(Exception):
        raw = json.loads(p.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and isinstance(raw.get("vecs"), dict):
            return raw
    return {"model": _embed_model(), "vecs": {}}


def _save_cache(home: str | Path | None, cache: dict[str, Any]) -> None:
    vecs = cache.get("vecs") or {}
    if len(vecs) > MAX_CACHED_VECS:
        # Drop oldest by stored ts (insertion time).
        items = sorted(vecs.items(), key=lambda kv: float(kv[1].get("ts") or 0.0))
        cache["vecs"] = dict(items[-MAX_CACHED_VECS:])
    p = _cache_path(home)
    with suppress(Exception):
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(cache, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)


def _model_invalidated(cache: dict[str, Any]) -> dict[str, Any]:
    """Drop cached vectors when the embedding model changed (different space)."""
    if cache.get("model") != _embed_model() and _EMBED_OVERRIDE is None:
        return {"model": _embed_model(), "vecs": {}}
    return cache


def _embed_cached(texts: list[str], home: str | Path | None) -> dict[str, list[float]] | None:
    """Return {text: vector} for the given texts, embedding only cache misses.

    The network embed runs WITHOUT the lock held (so one slow endpoint call
    can't serialize all recall). The cache is re-read under the lock before the
    merge so a concurrent writer's entries aren't clobbered.
    """
    with _lock:
        cache = _model_invalidated(_load_cache(home))
        vecs: dict[str, Any] = cache.get("vecs") or {}
        result: dict[str, list[float]] = {}
        missing: list[str] = []
        for t in texts:
            row = vecs.get(_hash(t))
            if isinstance(row, dict) and isinstance(row.get("v"), list):
                result[t] = [float(x) for x in row["v"]]
            else:
                missing.append(t)
        missing = list(dict.fromkeys(missing))

    if not missing:
        return result or None

    fresh = embed_texts(missing)  # network I/O — deliberately outside the lock
    if fresh is None:
        return result or None      # embedder unavailable; return cache hits only

    now = time.time()
    with _lock:
        cache = _model_invalidated(_load_cache(home))  # re-read; don't clobber peers
        vecs = cache.get("vecs") or {}
        for t, v in zip(missing, fresh, strict=False):
            result[t] = v
            vecs[_hash(t)] = {"v": v, "ts": now, "t": t[:80]}
        cache["vecs"] = vecs
        _save_cache(home, cache)
    return result or None


def semantic_scores(
    query: str, texts: Sequence[str], *, home: str | Path | None = None
) -> dict[str, float] | None:
    """Cosine similarity of *query* against each text, or None if unavailable.

    Returns a {text: score in [-1, 1]} map. None means "no embedder" — callers
    keep their keyword ranking unchanged.

    The query is embedded TRANSIENTLY and never written to the on-disk cache:
    queries are one-offs, and persisting them would evict the durable content
    vectors the cache exists to serve (its ring keeps newest-by-ts). Only the
    candidate content lines are cached.
    """
    q = (query or "").strip()
    uniq = [t for t in dict.fromkeys(str(t or "") for t in texts) if t]
    if len(q) < 2 or not uniq:
        return None
    qres = embed_texts([q])  # transient; also the cheap availability probe
    if not qres:
        return None
    qv = qres[0]
    cmap = _embed_cached(uniq, home)
    if not cmap:
        return None
    out: dict[str, float] = {}
    for t in uniq:
        tv = cmap.get(t)
        if tv is not None:
            out[t] = cosine(qv, tv)
    return out or None


def semantic_available() -> bool:
    """Cheap check: is an embedder plausibly reachable right now?"""
    if _EMBED_OVERRIDE is not None:
        return True
    if time.time() < _unavailable_until:
        return False
    return bool(_embed_url())
