"""Offline NanoToken family weight packs (tiktoken/Gigatoken-class heuristics).

Not full BPE merges — compact class-weight tables per encoding family, loadable
from this module or optional JSON overrides under ~/.remedy/token_tables/.
"""

from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# Baseline density: sum of class weights ≈ token count
BASE_WEIGHTS: dict[str, float] = {
    "ascii_word": 0.25,  # ~4 chars/token
    "space": 0.15,
    "code_punct": 0.45,
    "digit": 0.30,
    "cjk": 1.0,
    "emoji": 0.8,
    "other": 0.35,
}

# Family packs: scales applied to BASE_WEIGHTS + msg framing overhead
FAMILY_PACKS: dict[str, dict[str, Any]] = {
    "cl100k": {
        "label": "OpenAI-compat (cl100k / o200k-ish)",
        "scales": {
            "ascii_word": 1.0,
            "space": 1.0,
            "code_punct": 1.05,
            "digit": 1.0,
            "cjk": 0.95,
            "emoji": 1.1,
            "other": 1.0,
        },
        "msg_overhead": 4,
        # Multipliers for very code-heavy blobs (ratio of punct/alnum)
        "code_boost": 1.08,
        "prose_boost": 0.98,
    },
    "anthropic": {
        "label": "Anthropic-ish",
        "scales": {
            "ascii_word": 1.04,
            "space": 0.98,
            "code_punct": 1.18,
            "digit": 1.02,
            "cjk": 1.05,
            "emoji": 1.08,
            "other": 1.08,
        },
        "msg_overhead": 5,
        "code_boost": 1.12,
        "prose_boost": 1.0,
    },
    "deepseek": {
        "label": "DeepSeek / code-dense",
        "scales": {
            "ascii_word": 0.97,
            "space": 0.94,
            "code_punct": 1.2,
            "digit": 1.0,
            "cjk": 1.08,
            "emoji": 1.0,
            "other": 1.02,
        },
        "msg_overhead": 4,
        "code_boost": 1.15,
        "prose_boost": 0.97,
    },
    "local": {
        "label": "Local / demo",
        "scales": {
            "ascii_word": 1.0,
            "space": 1.0,
            "code_punct": 1.0,
            "digit": 1.0,
            "cjk": 1.0,
            "emoji": 1.0,
            "other": 1.0,
        },
        "msg_overhead": 3,
        "code_boost": 1.0,
        "prose_boost": 1.0,
    },
    # Extra pack for Gemini-class (still OpenAI-compat transport often)
    "gemini": {
        "label": "Gemini-ish",
        "scales": {
            "ascii_word": 1.02,
            "space": 1.0,
            "code_punct": 1.08,
            "digit": 1.0,
            "cjk": 1.0,
            "emoji": 1.05,
            "other": 1.02,
        },
        "msg_overhead": 4,
        "code_boost": 1.06,
        "prose_boost": 0.99,
    },
}

_lock = threading.Lock()
_loaded_overrides: dict[str, dict[str, Any]] | None = None


def _home_token_tables_dir() -> Path | None:
    try:
        from remedy.core.security import get_home_dir

        return get_home_dir() / "token_tables"
    except Exception:
        return Path("~/.remedy/token_tables").expanduser()


def load_family_overrides(force: bool = False) -> dict[str, dict[str, Any]]:
    """Load optional JSON packs from ~/.remedy/token_tables/<family>.json."""
    global _loaded_overrides
    with _lock:
        if _loaded_overrides is not None and not force:
            return _loaded_overrides
        out: dict[str, dict[str, Any]] = {}
        root = _home_token_tables_dir()
        if root is None or not root.is_dir():
            _loaded_overrides = out
            return out
        for path in root.glob("*.json"):
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(data, dict) and "scales" in data:
                    out[path.stem.lower()] = data
            except Exception as e:
                logger.debug("token table load failed %s: %s", path, e)
        _loaded_overrides = out
        return out


def get_family_pack(family: str) -> dict[str, Any]:
    """Return merged pack (builtin + optional user JSON override)."""
    fam = (family or "cl100k").lower()
    base = dict(FAMILY_PACKS.get(fam) or FAMILY_PACKS["cl100k"])
    overrides = load_family_overrides()
    if fam in overrides:
        ov = overrides[fam]
        scales = dict(base.get("scales") or {})
        scales.update(ov.get("scales") or {})
        base["scales"] = scales
        for k in ("msg_overhead", "code_boost", "prose_boost", "label"):
            if k in ov:
                base[k] = ov[k]
    return base


def resolved_weights(family: str) -> dict[str, float]:
    pack = get_family_pack(family)
    scales = pack.get("scales") or {}
    return {
        k: float(BASE_WEIGHTS[k]) * float(scales.get(k, 1.0)) for k in BASE_WEIGHTS
    }


def msg_overhead(family: str) -> int:
    return int(get_family_pack(family).get("msg_overhead") or 4)


def content_boost(family: str, text: str) -> float:
    """Slight boost when text is code-dense vs prose."""
    if not text or len(text) < 40:
        return 1.0
    pack = get_family_pack(family)
    sample = text[:4000]
    punct = sum(1 for c in sample if not c.isalnum() and not c.isspace())
    ratio = punct / max(1, len(sample))
    if ratio >= 0.18:
        return float(pack.get("code_boost") or 1.0)
    if ratio <= 0.08:
        return float(pack.get("prose_boost") or 1.0)
    return 1.0


def list_families() -> list[dict[str, Any]]:
    overrides = load_family_overrides()
    out = []
    for fid, pack in FAMILY_PACKS.items():
        out.append(
            {
                "id": fid,
                "label": pack.get("label"),
                "msg_overhead": pack.get("msg_overhead"),
                "overridden": fid in overrides,
            }
        )
    return out
