"""Compress quality review — did Session Brief keep the right facts?"""

from __future__ import annotations

import re
from typing import Any

from remedy.memory.harness.brief import SessionBrief, brief_to_context_block
from remedy.memory.harness.compressor import extract_paths_from_text

_DECISION_HINT = re.compile(
    r"(?:decided to|will use|choosing|we(?:'ll| will) use|strategy:)\s+([^.!?\n]{4,100})",
    re.I,
)


def _norm_path(p: str) -> str:
    return (p or "").strip().replace("\\", "/").lower()


def _clean_path_candidate(p: str) -> str:
    """Trim path extractor over-match (trailing prose after a real file)."""
    p = (p or "").strip().strip("'\"`")
    # Stop at common prose boundaries after a file extension
    m = re.search(
        r"((?:[A-Za-z]:)?[\\/][\w.\\/ -]{2,}?\.(?:py|ts|tsx|js|jsx|rs|go|md|toml|json|yml|yaml|css|html|txt))\b",
        p,
        re.I,
    )
    if m:
        return m.group(1).strip()
    m2 = re.search(
        r"([\w.-]+\.(?:py|ts|tsx|js|jsx|rs|go|md|toml|json|yml|yaml|css|html|txt))\b",
        p,
        re.I,
    )
    if m2:
        return m2.group(1).strip()
    return p


def extract_fact_candidates(messages: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    """Pull paths + decision-like phrases from recent history (pre-compress ground truth)."""
    paths: list[str] = []
    decisions: list[str] = []
    seen_p: set[str] = set()
    seen_d: set[str] = set()
    for m in messages or []:
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        # Prefer recent user + assistant spans
        role = m.get("role")
        if role not in ("user", "assistant", "tool", "system"):
            continue
        for p in extract_paths_from_text(content, limit=30):
            p = _clean_path_candidate(p)
            k = _norm_path(p)
            if k and k not in seen_p and len(k) > 3:
                seen_p.add(k)
                paths.append(p)
        if role in ("user", "assistant"):
            for match in _DECISION_HINT.finditer(content):
                d = match.group(1).strip()
                dk = d.lower()
                if dk and dk not in seen_d:
                    seen_d.add(dk)
                    decisions.append(d)
    return {"paths": paths[:40], "decisions": decisions[:20]}


def review_compress_quality(
    *,
    messages_before: list[dict[str, Any]] | None,
    brief: SessionBrief | None,
    tokens_before: int = 0,
    tokens_after: int = 0,
) -> dict[str, Any]:
    """Score whether brief retained important paths/decisions from history.

    Returns score in [0, 1], lists of kept/lost facts, and human summary.
    """
    facts = extract_fact_candidates(messages_before)
    paths = facts["paths"]
    decisions = facts["decisions"]

    brief_blob = ""
    if brief is not None:
        brief_blob = (
            brief_to_context_block(brief, max_chars=4000)
            + " "
            + " ".join(brief.artifacts or [])
            + " "
            + " ".join(brief.decisions or [])
            + " "
            + " ".join(brief.key_paths or [])
        )
    brief_l = brief_blob.lower().replace("\\", "/")

    paths_kept: list[str] = []
    paths_lost: list[str] = []
    for p in paths:
        # Keep if basename or full path appears in brief
        base = p.replace("\\", "/").rsplit("/", 1)[-1].lower()
        full = _norm_path(p)
        if full in brief_l or (base and len(base) > 2 and base in brief_l):
            paths_kept.append(p)
        else:
            paths_lost.append(p)

    dec_kept: list[str] = []
    dec_lost: list[str] = []
    for d in decisions:
        # Token overlap: at least 2 significant words from decision in brief
        words = [w for w in re.findall(r"[a-z0-9_\-]{3,}", d.lower()) if w not in (
            "the", "and", "for", "with", "that", "this", "from", "will", "use"
        )]
        if not words:
            dec_kept.append(d)
            continue
        hits = sum(1 for w in words[:6] if w in brief_l)
        if hits >= max(1, min(2, len(words) // 2)):
            dec_kept.append(d)
        else:
            dec_lost.append(d)

    n_path = len(paths) or 0
    n_dec = len(decisions) or 0
    path_score = (len(paths_kept) / n_path) if n_path else 1.0
    dec_score = (len(dec_kept) / n_dec) if n_dec else 1.0
    # Weight paths higher for coding agents; neutral high score if nothing extractable
    score = (
        0.65 * path_score + 0.35 * dec_score if (n_path or n_dec) else 0.85
    )

    # Token reduction bonus does not inflate quality — report separately
    reduction = 0.0
    if tokens_before > 0 and tokens_after >= 0:
        reduction = max(0.0, 1.0 - (tokens_after / max(1, tokens_before)))

    summary_parts = []
    if n_path:
        summary_parts.append(f"paths {len(paths_kept)}/{n_path}")
    if n_dec:
        summary_parts.append(f"decisions {len(dec_kept)}/{n_dec}")
    if tokens_before:
        summary_parts.append(
            f"tokens {tokens_before}→{tokens_after} ({reduction*100:.0f}% less est.)"
        )

    return {
        "score": round(min(1.0, max(0.0, score)), 3),
        "paths_total": n_path,
        "paths_kept": len(paths_kept),
        "paths_lost": len(paths_lost),
        "paths_lost_sample": paths_lost[:8],
        "decisions_total": n_dec,
        "decisions_kept": len(dec_kept),
        "decisions_lost": len(dec_lost),
        "decisions_lost_sample": dec_lost[:5],
        "token_reduction_pct": round(reduction * 100, 1),
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "summary": ", ".join(summary_parts) or "no extractable facts",
        "ok": score >= 0.55 and len(paths_lost) <= max(2, n_path // 3),
    }
