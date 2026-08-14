"""Machine-native working-memory middleman (experimental).

The current in-app memory leans on prose: summaries, a SessionBrief, and pruned
history are *narrated* back to the model. That is a human-memory imitation and it
is lossy — the structured truth (file paths, symbol names, exit codes, diffs,
content hashes) is flattened into sentences and thrown away.

The middleman inverts that: Remedy holds the session's structured truth in a
content-addressed store and, on each turn, *projects* only the slice the current
task needs into the context window. Cheap handles and a query-keyed index live in
context; the full artifact bodies stay offloaded and are pulled back only on
demand.

Machine-native primitives (deliberately no human-memory metaphors):

- **Content-addressing** (SHA-256): every item is immutable and idempotent — the
  same body always has the same key, so writes dedup for free and equality is O(1).
- **Provenance edges** (path / tool / session) instead of chronology: locality is
  *what you touched*, not *when you said it*. A turn about ``file_x`` retrieves
  only memory that mentions ``file_x``.
- **Token-scored retrieval** keyed by the *current* message, not recency. This is
  a BM25-lite over an inverted token index.
- **Budget projection**: the middleman returns exactly the top-k hits that fit a
  token budget — so a 4k local window gets a *minimal, sufficient* block instead
  of a full dump.
- **Handle resolution**: a handle in context (``remedy-mm://<sha>``) is cheap; the
  real body is fetched lazily when the model asks for it.

RAM is the hot index. ``remedy.memory.cas`` is the eternal plane: write-through
on put, hydrate on session open, FTS only on a RAM miss. Same SHA is the same
object after restart. The process is not the memory.

This is the reference implementation for ``docs/RESEARCH_memory_middleman.md``.
"""

from __future__ import annotations

import hashlib
import re
import threading
import time
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

# Prefix marking an offloaded handle in a message.
HANDLE_PREFIX = "remedy-mm://"
# Score below which a hit is treated as noise.
_MIN_SCORE = 0.05
# Default cap on a stored tool-result body (bounded in-memory working memory).
# Offload (harness/offload.py) keeps the full body on disk for handle reads.
_DEFAULT_BODY_CAP = 2_000

_TOKEN_RE = re.compile(r"[a-z0-9_]+")
_STOP = frozenset(
    {
        "the", "a", "an", "and", "or", "of", "to", "in", "on", "for", "with",
        "is", "are", "was", "were", "be", "by", "at", "this", "that", "it",
        "from", "as", "we", "you", "i", "he", "she", "they", "do", "does",
        "did", "have", "has", "had", "not", "but", "so", "if", "then", "else",
        "its", "it's", "about", "into", "over", "after", "before", "there",
        "where", "what", "when", "who", "how", "why", "which",
    }
)


def tokenize(text: str) -> list[str]:
    return [t for t in _TOKEN_RE.findall((text or "").lower()) if t not in _STOP]


def content_key(body: str) -> str:
    """SHA-256 content address for a body (dedup + immutable identity)."""
    return hashlib.sha256((body or "").encode("utf-8", errors="replace")).hexdigest()


def make_handle(key: str, *, kind: str = "", path: str = "") -> str:
    """A compact handle the model can put in context and later resolve."""
    short = key[:12]
    bits = []
    if kind:
        bits.append(kind)
    if path:
        bits.append(path)
    label = ":".join(bits) if bits else "item"
    return f"{HANDLE_PREFIX}{short}#{label}"


def is_handle(text: str) -> bool:
    return HANDLE_PREFIX in (text or "")


@dataclass
class MemoryItem:
    """One immutable, content-addressed memory unit with provenance."""

    key: str
    kind: str
    body: str
    path: str = ""
    tool: str = ""
    session_id: str = ""
    tags: tuple[str, ...] = ()
    ts: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "kind": self.kind,
            "path": self.path,
            "tool": self.tool,
            "session_id": self.session_id,
            "tags": list(self.tags),
            "ts": self.ts,
            "body": self.body,
        }


@dataclass
class Hit:
    item: MemoryItem
    score: float
    snippet: str


class MiddlemanMemory:
    """Content-addressed, provenance-indexed, token-scored memory store."""

    def __init__(self) -> None:
        self._by_key: dict[str, MemoryItem] = {}
        self._path_index: dict[str, set[str]] = {}
        self._tool_index: dict[str, set[str]] = {}
        self._session_index: dict[str, set[str]] = {}
        # key -> {token: count} (inverted index, built lazily on first search)
        self._token_postings: dict[str, dict[str, int]] = {}
        # document frequency per token (for IDF)
        self._df: dict[str, int] = {}
        self._lock = threading.Lock()
        self._hydrated = False

    def load_item(self, item: MemoryItem) -> bool:
        """Index an existing object (hydrate). Does not persist. False if dup."""
        if not item or not item.key or item.key in self._by_key:
            return False
        with self._lock:
            return self._index_item(item)

    def _index_item(self, item: MemoryItem) -> bool:
        if item.key in self._by_key:
            return False
        self._by_key[item.key] = item
        for token, freq in _counts(item.body).items():
            self._token_postings.setdefault(item.key, {})[token] = freq
            self._df[token] = self._df.get(token, 0) + 1
        if item.path:
            self._path_index.setdefault(item.path, set()).add(item.key)
        if item.tool:
            self._tool_index.setdefault(item.tool, set()).add(item.key)
        if item.session_id:
            self._session_index.setdefault(item.session_id, set()).add(item.key)
        return True

    def ensure_hydrated(self, session_id: str = "") -> None:
        if self._hydrated:
            return
        self._hydrated = True
        try:
            from remedy.memory.cas import get_cas

            cas = get_cas()
            if cas is not None:
                cas.hydrate(self, session_id=session_id)
        except Exception:
            pass

    # ---- ingestion ---------------------------------------------------------

    def put(
        self,
        body: str,
        *,
        kind: str = "note",
        path: str = "",
        tool: str = "",
        session_id: str = "",
        tags: Iterable[str] = (),
        body_cap: int | None = None,
    ) -> str:
        """Store a body, return its content address. Idempotent + deduped.

        ``body_cap`` bounds the retained body (e.g. tool results) so the in-memory
        working set stays small; the full content lives in the offload store.
        """
        body = (body or "").strip()
        if not body:
            return ""
        if body_cap and len(body) > body_cap:
            body = body[:body_cap] + "\n…[middleman cap]"
        key = content_key(body)
        item = MemoryItem(
            key=key,
            kind=kind,
            body=body,
            path=(path or "").strip(),
            tool=(tool or "").strip(),
            session_id=(session_id or "").strip(),
            tags=tuple(t for t in tags if t),
        )
        with self._lock:
            if key not in self._by_key:
                self._index_item(item)
        try:
            from remedy.memory.cas import get_cas

            cas = get_cas()
            if cas is not None:
                cas.put_item(item)
        except Exception:
            pass
        return key

    def put_many(self, items: Iterable[dict[str, Any]]) -> list[str]:
        return [self.put(**it) for it in items]

    # ---- retrieval ---------------------------------------------------------

    def get(self, key: str) -> str | None:
        """Resolve a content address (or handle) to its full body."""
        if not key:
            return None
        if HANDLE_PREFIX in key:
            # handle "remedy-mm://<short>#..." → match by short prefix
            short = key.split(HANDLE_PREFIX, 1)[1].split("#", 1)[0]
            for k in self._by_key:
                if k.startswith(short):
                    return self._by_key[k].body
            key = short
        item = self._by_key.get(key)
        if item is not None:
            return item.body
        try:
            from remedy.memory.cas import get_cas

            cas = get_cas()
            if cas is not None:
                cold = cas.get(key)
                if cold is not None:
                    self.load_item(cold)
                    return cold.body
        except Exception:
            pass
        return None

    def item(self, key: str) -> MemoryItem | None:
        return self._by_key.get(key)

    def keys_for_path(self, path: str) -> set[str]:
        return set(self._path_index.get(path, set()))

    def _candidate_keys(
        self,
        *,
        paths: Iterable[str] | None = None,
        tools: Iterable[str] | None = None,
        session_id: str | None = None,
    ) -> set[str]:
        cands: set[str] | None = None
        for index, selectors in (
            (self._path_index, paths),
            (self._tool_index, tools),
            (self._session_index, [session_id] if session_id else None),
        ):
            if not selectors:
                continue
            sel = {s for s in selectors if s}
            if not sel:
                continue
            union: set[str] = set()
            for s in sel:
                union |= index.get(s, set())
            cands = union if cands is None else (cands & union)
        if cands is None:
            cands = set(self._by_key.keys())
        return cands

    def search(
        self,
        query: str,
        *,
        paths: Iterable[str] | None = None,
        tools: Iterable[str] | None = None,
        session_id: str | None = None,
        kinds: Iterable[str] | None = None,
        top_k: int = 5,
    ) -> list[Hit]:
        """Token-scored (BM25-lite) search, provenance-filtered, by relevance."""
        q_tokens = tokenize(query)
        cands = self._candidate_keys(paths=paths, tools=tools, session_id=session_id)
        if kinds:
            kinds = set(kinds)
            cands = {k for k in cands if self._by_key[k].kind in kinds}
        if not q_tokens:
            return []
        total_docs = max(1, len(self._by_key))
        scored: list[Hit] = []
        for key in cands:
            posting = self._token_postings.get(key, {})
            score = 0.0
            for tok in q_tokens:
                tf = posting.get(tok, 0)
                if tf == 0:
                    continue
                idf = 1.0 + _log((total_docs + 1) / (1 + self._df.get(tok, 0)))
                score += idf * (1 + _log(tf))
            if score <= _MIN_SCORE:
                continue
            scored.append(
                Hit(
                    item=self._by_key[key],
                    score=score,
                    snippet=_snippet(self._by_key[key].body),
                )
            )
        scored.sort(key=lambda h: h.score, reverse=True)
        if len(scored) >= top_k:
            return scored[:top_k]
        # Eternal plane: RAM miss → FTS on disk, then stay hot.
        try:
            from remedy.memory.cas import get_cas

            cas = get_cas()
        except Exception:
            cas = None
        if cas is None:
            return scored[:top_k]
        have = {h.item.key for h in scored}
        try:
            extra = cas.search_fts(
                query,
                limit=max(1, top_k - len(scored)),
                exclude=have,
                kinds=kinds,
            )
        except Exception:
            extra = []
        for item in extra:
            self.load_item(item)
            scored.append(Hit(item=item, score=_MIN_SCORE + 0.01, snippet=_snippet(item.body)))
        scored.sort(key=lambda h: h.score, reverse=True)
        return scored[:top_k]

    # ---- projection (the middleman's core trick) ---------------------------

    def project(
        self,
        query: str,
        *,
        budget_tokens: int = 600,
        paths: Iterable[str] | None = None,
        tools: Iterable[str] | None = None,
        session_id: str | None = None,
        kinds: Iterable[str] | None = None,
        top_k: int = 6,
    ) -> str:
        """Return a compact, budget-bounded structured block for the context.

        Each line is ``- [kind] path: snippet …[handle]``. The block stops as soon
        as the token budget is exhausted, so a small local window gets a minimal,
        sufficient slice instead of a dump. Handles let the model lazily pull the
        full body with :meth:`resolve`.
        """
        hits = self.search(
            query,
            paths=paths,
            tools=tools,
            session_id=session_id,
            kinds=kinds,
            top_k=top_k,
        )
        if not hits:
            return ""
        lines: list[str] = []
        used = 0
        for h in hits:
            it = h.item
            meta = it.kind
            if it.path:
                meta += f"@{it.path}"
            line = f"- [{meta}] {h.snippet} …[{make_handle(it.key, kind=it.kind, path=it.path)}]"
            # rough token estimate: ~4 chars/token
            approx = max(1, len(line) // 4)
            if lines and used + approx > budget_tokens:
                break
            lines.append(line)
            used += approx
        return "\n".join(lines)

    def resolve(self, text: str) -> str:
        """Replace any handles in *text* with their full bodies (lazy pull)."""
        if not text:
            return text
        out = text
        for _key, item in self._by_key.items():
            handle = make_handle(item.key, kind=item.kind, path=item.path)
            if handle in out:
                out = out.replace(handle, item.body)
        return out

    # ---- introspection -----------------------------------------------------

    def __len__(self) -> int:
        return len(self._by_key)

    def snapshot(self) -> dict[str, Any]:
        return {
            "count": len(self._by_key),
            "kinds": _tally(item.kind for item in self._by_key.values()),
            "paths": sorted(self._path_index),
            "tools": sorted(self._tool_index),
        }


def _counts(text: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for t in tokenize(text):
        out[t] = out.get(t, 0) + 1
    return out


def _log(x: float) -> float:
    # ln with a floor to keep IDF finite
    return float(__import__("math").log(max(x, 1e-9)))


def _snippet(body: str, max_chars: int = 120) -> str:
    s = " ".join((body or "").split())
    return s[:max_chars] + ("…" if len(s) > max_chars else "")


def _tally(iterable: Iterable[str]) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in iterable:
        out[v] = out.get(v, 0) + 1
    return out


# ---- process-global session registry (the middleman service) ---------------

_session_registry: dict[str, MiddlemanMemory] = {}
_session_registry_lock = threading.Lock()


def get_session_middleman(session_id: str) -> MiddlemanMemory:
    """Return (creating if needed) the per-session middleman store.

    This is the "middleman" the loop writes to and projects from: one working
    memory per session, content-addressed and query-retrievable, in-process for
    the lifetime of the serve process.
    """
    sid = (session_id or "").strip() or "anon"
    with _session_registry_lock:
        mm = _session_registry.get(sid)
        if mm is None:
            mm = MiddlemanMemory()
            _session_registry[sid] = mm
    mm.ensure_hydrated(sid)
    return mm


def forget_session_middleman(session_id: str) -> None:
    """Drop a session's RAM index. The eternal CAS is not deleted."""
    sid = (session_id or "").strip() or "anon"
    with _session_registry_lock:
        _session_registry.pop(sid, None)


def reset_middleman_state() -> None:
    """Tests: drop every RAM index. Does not wipe the CAS file."""
    with _session_registry_lock:
        _session_registry.clear()


def ingest_tool_result(
    *,
    session_id: str,
    content: str,
    tool: str = "",
    path: str = "",
) -> str:
    """Non-blocking ingest of a tool result into the session middleman.

    Returns the content address ('' when content is empty). Bounded to a small
    body so the in-memory working set stays lean; the full output lives in the
    offload store and is readable via handles.
    """
    if not (content or "").strip():
        return ""
    return get_session_middleman(session_id).put(
        content,
        kind="tool",
        tool=(tool or "").strip(),
        path=(path or "").strip(),
        session_id=session_id,
        body_cap=_DEFAULT_BODY_CAP,
    )
