"""Cache-first Skills Library ranking for soft install suggestions.

Hot path: disk cache / monorepo local only — never HTTP.
Background: callers may refresh via get_skills_catalog(refresh=True).
"""

from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from remedy.skills.library.catalog import (
    SkillCatalogEntry,
    SkillsCatalog,
    _home_cache_dir,
    _load_signed_local,
    _repo_local_catalog,
)
from remedy.skills.library.keys import CATALOG_PUBLIC_KEY_B64
from remedy.skills.library.security import verify_catalog_signature

logger = logging.getLogger(__name__)

DEFAULT_MIN_SCORE = 0.35
DEFAULT_MIN_QUERY_CHARS = 24

_TOKEN_RE = re.compile(r"[^a-z0-9]+")
_CHATTY = re.compile(
    r"^\s*(hi|hello|hey|thanks|thank you|ok|okay|yes|no|sure|cool|lol)\s*[.!]?\s*$",
    re.I,
)
_TOOLISH = re.compile(
    r"\b("
    r"implement|fix|debug|build|test|review|refactor|deploy|ci|pr\b|pull request|"
    r"commit|lint|design|write|create|setup|configure|pipeline|security|a11y|"
    r"accessibility|docker|api|frontend|backend|skill|procedure|playbook|"
    r"conventional|changelog|auth|session|cors|benchmark"
    r")\b",
    re.I,
)


def _tokenize(text: str) -> set[str]:
    return {t for t in _TOKEN_RE.split((text or "").lower()) if len(t) >= 2}


@dataclass
class LibraryHit:
    id: str
    name: str
    description: str
    score: float
    version: str = "1.0.0"
    tags: list[str] = field(default_factory=list)
    reason: str = ""

    def to_public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "description": (self.description or "")[:200],
            "score": round(float(self.score), 4),
            "version": self.version,
            "tags": list(self.tags)[:12],
            "reason": self.reason,
        }


@dataclass
class _IndexEntry:
    id: str
    name: str
    description: str
    version: str
    tags: list[str]
    tools: list[str]
    name_tokens: set[str]
    desc_tokens: set[str]
    tag_tokens: set[str]
    all_tokens: set[str]


class LibraryIndex:
    """In-process index of catalog summaries (mtime-keyed)."""

    def __init__(self) -> None:
        self.entries: list[_IndexEntry] = []
        self.source: str = "none"
        self.mtime: float = -1.0
        self.path: str = ""
        self.needs_refresh: bool = False
        self.build_ms: float = 0.0

    def __len__(self) -> int:
        return len(self.entries)


_index_lock = threading.Lock()
_index: LibraryIndex | None = None
_suppress: dict[str, set[str]] = {}
_suppress_lock = threading.Lock()


def suppress_suggest(session_id: str, skill_id: str) -> None:
    sid = (session_id or "").strip() or "_default"
    kid = (skill_id or "").strip()
    if not kid:
        return
    with _suppress_lock:
        _suppress.setdefault(sid, set()).add(kid)


def is_suppressed(session_id: str, skill_id: str) -> bool:
    sid = (session_id or "").strip() or "_default"
    with _suppress_lock:
        return (skill_id or "") in _suppress.get(sid, set())


def clear_session_suppress(session_id: str) -> None:
    sid = (session_id or "").strip() or "_default"
    with _suppress_lock:
        _suppress.pop(sid, None)


def get_skills_catalog_cached(
    home: Path | str | None = None,
    *,
    force_reload: bool = False,
) -> tuple[SkillsCatalog | None, bool]:
    """Load verified catalog from disk cache or monorepo — never HTTP.

    Returns (catalog_or_None, needs_refresh).
    """
    home_p = Path(home).expanduser() if home else Path.home() / ".remedy"
    cache_dir = _home_cache_dir(home_p)
    cache_file = cache_dir / "catalog.json"
    cache_sig = cache_dir / "catalog.json.sig"
    pubkey = os.environ.get("REMEDY_SKILLS_CATALOG_PUBKEY") or CATALOG_PUBLIC_KEY_B64

    if cache_file.is_file() and cache_sig.is_file():
        try:
            data = cache_file.read_bytes()
            sig = cache_sig.read_text(encoding="utf-8").strip()
            verify_catalog_signature(data, sig, public_key_b64=pubkey)
            cat = SkillsCatalog.model_validate(json.loads(data.decode("utf-8")))
            cat.source = "cache"
            age = time.time() - cache_file.stat().st_mtime
            needs = age >= 86_400 or force_reload
            return cat, needs
        except Exception as e:
            logger.debug("skills catalog cache unusable: %s", e)

    local = _repo_local_catalog()
    if local is not None:
        try:
            cat = _load_signed_local(local, public_key_b64=pubkey)
            return cat, True
        except Exception as e:
            logger.debug("local skills catalog failed: %s", e)

    return None, True


def _entry_from_catalog(e: SkillCatalogEntry) -> _IndexEntry | None:
    if (e.status or "published").lower() not in ("published", "active", ""):
        return None
    name = (e.name or e.id or "").strip()
    if not name:
        return None
    desc = (e.description or "").strip()
    tags = list(e.tags or [])
    tools = list(e.tools or [])
    name_tokens = _tokenize(name.replace("-", " ").replace("_", " "))
    desc_tokens = _tokenize(desc)
    tag_tokens = _tokenize(" ".join(tags))
    tool_tokens = _tokenize(" ".join(tools))
    all_t = name_tokens | desc_tokens | tag_tokens | tool_tokens
    return _IndexEntry(
        id=str(e.id or name),
        name=name,
        description=desc,
        version=str(e.version or "1.0.0"),
        tags=tags,
        tools=tools,
        name_tokens=name_tokens,
        desc_tokens=desc_tokens,
        tag_tokens=tag_tokens | tool_tokens,
        all_tokens=all_t,
    )


def build_library_index(
    home: Path | str | None = None,
    *,
    force_reload: bool = False,
) -> LibraryIndex:
    """Build or return cached LibraryIndex from disk catalog."""
    global _index
    home_p = Path(home).expanduser() if home else Path.home() / ".remedy"
    cache_file = _home_cache_dir(home_p) / "catalog.json"
    local = _repo_local_catalog()
    path_s = ""
    mtime = -1.0
    if cache_file.is_file():
        path_s = str(cache_file.resolve())
        try:
            mtime = cache_file.stat().st_mtime
        except OSError:
            mtime = -1.0
    elif local is not None:
        path_s = str(local.resolve())
        try:
            mtime = local.stat().st_mtime
        except OSError:
            mtime = -1.0

    with _index_lock:
        if (
            not force_reload
            and _index is not None
            and _index.path == path_s
            and _index.mtime == mtime
            and len(_index) > 0
        ):
            return _index

        t0 = time.perf_counter()
        cat, needs = get_skills_catalog_cached(home_p, force_reload=force_reload)
        idx = LibraryIndex()
        idx.needs_refresh = needs or cat is None
        idx.path = path_s
        idx.mtime = mtime
        if cat is not None:
            idx.source = cat.source
            for raw in cat.skills:
                ent = _entry_from_catalog(raw)
                if ent is not None:
                    idx.entries.append(ent)
        idx.build_ms = (time.perf_counter() - t0) * 1000.0
        _index = idx
        return idx


def invalidate_library_index() -> None:
    global _index
    with _index_lock:
        _index = None


def rank_library_skills(
    query: str,
    *,
    home: Path | str | None = None,
    installed_names: set[str] | None = None,
    limit: int = 5,
    min_score: float = DEFAULT_MIN_SCORE,
    session_id: str | None = None,
    mark_suppressed: bool = False,
) -> list[LibraryHit]:
    """Rank library packs for *query*. Filters installed + suppressed."""
    q = (query or "").strip()
    if len(q) < 8:
        return []
    if _CHATTY.match(q):
        return []
    q_tokens = _tokenize(q)
    if not q_tokens:
        return []

    idx = build_library_index(home)
    if not idx.entries:
        return []

    installed = {n.lower() for n in (installed_names or set()) if n}
    ql = q.lower()
    scored: list[LibraryHit] = []

    for e in idx.entries:
        if e.name.lower() in installed or e.id.lower() in installed:
            continue
        if session_id and (
            is_suppressed(session_id, e.id) or is_suppressed(session_id, e.name)
        ):
            continue

        overlap = len(q_tokens & e.all_tokens)
        name_hit = len(q_tokens & e.name_tokens)
        tag_hit = len(q_tokens & e.tag_tokens)
        if name_hit == 0 and tag_hit == 0 and overlap < 2:
            if ql not in e.name.lower() and not any(
                t in e.name.lower().replace("-", "") for t in q_tokens if len(t) >= 4
            ):
                continue

        substr = 0.0
        if ql and ql in e.name.lower():
            substr += 0.55
        if ql and ql in (e.description or "").lower():
            substr += 0.2
        for t in q_tokens:
            if len(t) >= 4 and t in e.name.lower().replace("-", ""):
                substr += 0.08
                break

        text_score = (
            0.40 * min(1.0, overlap / max(1, len(q_tokens)))
            + 0.35 * min(1.0, name_hit / max(1, min(3, len(q_tokens))))
            + 0.15 * min(1.0, tag_hit / max(1, min(3, len(q_tokens))))
            + 0.10 * min(1.0, substr)
        )
        if text_score < min_score:
            continue

        reasons: list[str] = []
        if name_hit:
            reasons.append("name match")
        if tag_hit:
            reasons.append("tags")
        if overlap >= 2:
            reasons.append("description overlap")
        scored.append(
            LibraryHit(
                id=e.id,
                name=e.name,
                description=e.description,
                score=text_score,
                version=e.version,
                tags=e.tags[:8],
                reason=", ".join(reasons) or "relevant",
            )
        )

    scored.sort(key=lambda h: (-h.score, h.name.lower()))
    out = scored[: max(1, min(20, limit))]
    if mark_suppressed and session_id and out:
        suppress_suggest(session_id, out[0].id)
    return out


def should_attempt_library_suggest(
    user_text: str,
    *,
    intent: str = "chat",
    min_chars: int = DEFAULT_MIN_QUERY_CHARS,
) -> bool:
    text = (user_text or "").strip()
    if len(text) < min_chars:
        return False
    if _CHATTY.match(text):
        return False
    intent = (intent or "chat").strip().lower()
    if intent in ("chat", "memory"):
        return bool(_TOOLISH.search(text))
    if intent in ("tool", "autonomous", "plan", "skill"):
        return True
    return bool(_TOOLISH.search(text))


def suggest_library_skill(
    user_text: str,
    *,
    intent: str = "chat",
    home: Path | str | None = None,
    installed_names: set[str] | None = None,
    installed_top_score: float | None = None,
    session_id: str | None = None,
    min_score: float = DEFAULT_MIN_SCORE,
    min_chars: int = DEFAULT_MIN_QUERY_CHARS,
    installed_cover_threshold: float = 0.55,
    mark_suggested: bool = True,
) -> LibraryHit | None:
    """Return at most one library suggestion, or None."""
    if not should_attempt_library_suggest(
        user_text, intent=intent, min_chars=min_chars
    ):
        return None
    if (
        installed_top_score is not None
        and float(installed_top_score) >= installed_cover_threshold
    ):
        return None

    hits = rank_library_skills(
        user_text,
        home=home,
        installed_names=installed_names,
        limit=3,
        min_score=min_score,
        session_id=session_id,
        mark_suppressed=False,
    )
    if not hits:
        return None
    best = hits[0]
    if mark_suggested and session_id:
        suppress_suggest(session_id, best.id)
    return best


def system_hint_for(hit: LibraryHit) -> str:
    return (
        f"[Library] Not installed: **{hit.name}** — {hit.description[:120]}"
        f"{'…' if len(hit.description) > 120 else ''}. "
        "Do not invent its procedure. Tell the user a Skills Library pack may help; "
        "they can Install (quarantine) then Trust in Skills → Library. "
        f"skill_id={hit.id}"
    )


def load_suggest_config(cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    out = {
        "enabled": True,
        "min_score": DEFAULT_MIN_SCORE,
        "min_query_chars": DEFAULT_MIN_QUERY_CHARS,
        "local_rerank": False,
        "installed_cover_threshold": 0.55,
    }
    if not isinstance(cfg, dict):
        return out
    root = cfg.get("skills") if isinstance(cfg.get("skills"), dict) else cfg
    sp = None
    if isinstance(root, dict):
        sp = root.get("library_suggest")
    if not isinstance(sp, dict):
        sp = (
            cfg.get("library_suggest")
            if isinstance(cfg.get("library_suggest"), dict)
            else None
        )
    if not isinstance(sp, dict):
        return out
    if "enabled" in sp:
        out["enabled"] = bool(sp.get("enabled"))
    if sp.get("min_score") is not None:
        try:
            out["min_score"] = float(sp["min_score"])
        except (TypeError, ValueError):
            pass
    if sp.get("min_query_chars") is not None:
        try:
            out["min_query_chars"] = int(sp["min_query_chars"])
        except (TypeError, ValueError):
            pass
    if "local_rerank" in sp:
        out["local_rerank"] = bool(sp.get("local_rerank"))
    if sp.get("installed_cover_threshold") is not None:
        try:
            out["installed_cover_threshold"] = float(sp["installed_cover_threshold"])
        except (TypeError, ValueError):
            pass
    return out


def schedule_catalog_refresh(home: Path | str | None = None) -> None:
    """Background thread: refresh remote catalog into disk cache (never blocks caller)."""
    home_p = Path(home).expanduser() if home else Path.home() / ".remedy"

    def _run() -> None:
        try:
            import asyncio

            from remedy.skills.library.catalog import get_skills_catalog

            async def _go() -> None:
                await get_skills_catalog(refresh=True, home=home_p)
                invalidate_library_index()
                build_library_index(home_p, force_reload=True)

            asyncio.run(_go())
        except Exception:
            logger.debug("background skills catalog refresh failed", exc_info=True)

    t = threading.Thread(target=_run, name="remedy-skills-catalog-refresh", daemon=True)
    t.start()
