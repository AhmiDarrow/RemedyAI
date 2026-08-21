"""Unified recall across Soul Field, Partner Memory, and Time Crystal.

One tool surface for the organism: “what do we know / feel / still owe?”
"""

from __future__ import annotations

import asyncio
import re
import time
from contextlib import suppress
from typing import Any

from remedy.memory.soul.field import load_soul_field
from remedy.memory.soul.inject import build_soul_context_block


def _tokens(q: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]{3,}", (q or "").lower()))


def _score(query_tokens: set[str], text: str) -> float:
    if not text:
        return 0.0
    if not query_tokens:
        return 0.15
    blob = set(re.findall(r"[a-z0-9]{3,}", text.lower()))
    if not blob:
        return 0.0
    hits = len(query_tokens & blob)
    return hits / max(1.0, len(query_tokens) ** 0.5)


#: Wall-clock budget for one recall (seconds). The semantic pass is skipped
#: once the keyword pass has eaten most of it; the async wrapper enforces the
#: whole thing as a hard ceiling.
RECALL_BUDGET_S = 15.0


def recall_unified(
    query: str = "",
    *,
    home: str | Any = None,
    memory: Any = None,
    session_id: str | None = None,
    limit: int = 12,
    profile: Any = None,
    budget_s: float = RECALL_BUDGET_S,
) -> str:
    """Return a ranked markdown brief for agent/tool use.

    Synchronous and blocking (file reads, an optional local embedding call).
    From the event loop, use :func:`recall_unified_async` — it runs this in a
    worker thread under a hard time budget. *profile* is a pre-loaded partner
    profile for callers that already awaited it; when given, *memory* is not
    consulted.
    """
    t0 = time.monotonic()
    budget = max(0.5, float(budget_s or RECALL_BUDGET_S))
    q = (query or "").strip()
    qtok = _tokens(q)
    hits: list[tuple[float, str, str]] = []  # score, source, line

    sf = load_soul_field(home)
    # Soul: episodes, pledges, tensions, habits
    for ep in sf.episodes:
        line = ep.line()
        s = _score(qtok, line) + 0.05
        if s > 0.1 or not qtok:
            hits.append((s, "episode", line))
    for p in sf.pledges:
        s = _score(qtok, p) + 0.2
        if s > 0.15 or not qtok:
            hits.append((s, "pledge", p))
    for t in sf.relational.open_threads:
        s = _score(qtok, t) + 0.25
        if s > 0.15 or not qtok:
            hits.append((s, "open_thread", t))
    for t in sf.relational.tensions:
        s = _score(qtok, t) + 0.2
        if s > 0.15 or not qtok:
            hits.append((s, "tension", t))
    for h in sf.self_habits:
        s = _score(qtok, h) + 0.1
        if s > 0.15 or not qtok:
            hits.append((s, "habit", h))
    for d in getattr(sf, "future_dreams", None) or []:
        s = _score(qtok, d) + 0.22
        if s > 0.15 or not qtok:
            hits.append((s, "dream", d))
    for les in sf.organism_lessons[-8:]:
        line = les.line()
        s = _score(qtok, line) + 0.1
        if s > 0.15 or not qtok:
            hits.append((s, "organism", line))

    # Time Crystal
    with suppress(Exception):
        from remedy.core.metabolism.time_crystal import get_time_crystal

        for sid in (session_id or "", "soul-life", "_default"):
            if not sid and sid != "":
                continue
            tc = get_time_crystal(sid or "_default")
            for f in list(tc.facts)[-40:]:
                s = _score(qtok, f.text) + (
                    0.3 if f.horizon == "life" else 0.15 if f.horizon == "project_week" else 0.05
                )
                if s > 0.2 or (not qtok and f.horizon in ("life", "project_week")):
                    hits.append((s, f"crystal:{f.horizon}", f.text))

    # Partner memory (sync profile if already loaded path — async store optional)
    if profile is not None:
        with suppress(Exception):
            from remedy.memory.partner_memory import rank_injectable_facts

            for f in rank_injectable_facts(profile, query=q, limit=16):
                s = _score(qtok, f.fact) + float(f.confidence) * 0.3
                hits.append((s, f"fact:{f.category}", f.fact))
    elif memory is not None:
        with suppress(Exception):
            from remedy.memory.partner_memory import rank_injectable_facts

            async def _facts() -> None:
                profile = await memory.get_or_create_profile()
                for f in rank_injectable_facts(profile, query=q, limit=16):
                    s = _score(qtok, f.fact) + float(f.confidence) * 0.3
                    hits.append((s, f"fact:{f.category}", f.fact))

            try:
                asyncio.get_running_loop()
            except RuntimeError:
                asyncio.run(_facts())

    # Semantic recall (optional, local): surface memories that MEAN what was
    # asked even with ZERO shared words. Purely additive — when no embedder is
    # reachable this whole block is a no-op and keyword ranking stands. It scans
    # the full local soul set (not just lines the keyword gate admitted), so a
    # meaning-match that shares no tokens with the query can still surface.
    # Dedupe below merges a line found both ways, keeping the higher score.
    if q and (time.monotonic() - t0) < budget * 0.6:
        with suppress(Exception):
            from remedy.memory.soul.semantic import semantic_scores

            src_by_line: dict[str, str] = {}

            def _add_cand(src: str, line: str) -> None:
                line = (line or "").strip()
                if line:
                    src_by_line.setdefault(line, src)

            for ep in sf.episodes:
                _add_cand("episode", ep.line())
            for p in sf.pledges:
                _add_cand("pledge", p)
            for t in sf.relational.open_threads:
                _add_cand("open_thread", t)
            for t in sf.relational.tensions:
                _add_cand("tension", t)
            for h in sf.self_habits:
                _add_cand("habit", h)
            for d in getattr(sf, "future_dreams", None) or []:
                _add_cand("dream", d)
            for les in sf.organism_lessons[-8:]:
                _add_cand("organism", les.line())

            sem = semantic_scores(q, list(src_by_line.keys()), home=home)
            if sem:
                for line, score in sem.items():
                    if score >= 0.55:  # a real meaning match, not noise
                        base = src_by_line.get(line, "memory")
                        hits.append((0.4 + 0.6 * float(score), f"{base}~meaning", line))

    # Dedupe by line text
    seen: set[str] = set()
    ranked: list[tuple[float, str, str]] = []
    for s, src, line in sorted(hits, key=lambda x: -x[0]):
        key = re.sub(r"\s+", " ", line.lower())[:120]
        if key in seen:
            continue
        seen.add(key)
        ranked.append((s, src, line))
        if len(ranked) >= max(1, limit):
            break

    if not ranked:
        # Fallback: inject slim soul block
        block = build_soul_context_block(home=home, include_contract=False, max_chars=800)
        if block:
            return "Soul recall (no query hits — field snapshot):\n" + block
        return "Nothing recalled yet. Talk, build, and remember — the field densifies over turns."

    lines = ["**Unified recall**" + (f" for “{q[:80]}”" if q else "") + ":"]
    for s, src, line in ranked:
        lines.append(f"- ({src} · {s:.2f}) {line[:220]}")
    return "\n".join(lines)


async def recall_unified_async(
    query: str = "",
    *,
    home: str | Any = None,
    memory: Any = None,
    session_id: str | None = None,
    limit: int = 12,
    budget_s: float = RECALL_BUDGET_S,
) -> str:
    """Event-loop-safe recall: partner profile awaited here, the rest in a thread.

    The synchronous body reads the soul field, the time crystal and (when a
    local embedder is configured) makes a blocking HTTP call — all of which
    used to run on the event loop and stall every other request. A hard
    ``budget_s`` ceiling turns a hung embedder into a short note, not a freeze.
    """
    profile = None
    if memory is not None:
        with suppress(Exception):
            profile = await asyncio.wait_for(
                memory.get_or_create_profile(), timeout=min(5.0, budget_s)
            )
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(
                recall_unified,
                query or "",
                home=home,
                memory=None,
                session_id=session_id,
                limit=limit,
                profile=profile,
                budget_s=budget_s,
            ),
            timeout=budget_s,
        )
    except TimeoutError:
        return (
            f"Recall timed out after {budget_s:.0f}s (the soul field or a local "
            "embedder is slow). Try a shorter query or memory_search."
        )
