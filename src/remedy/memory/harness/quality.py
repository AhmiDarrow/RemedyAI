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
# Binding owner language. "must not" is listed inside "must" so both forms hit.
_CONSTRAINT_RE = re.compile(
    r"\b(?:must(?:\s+not)?|never|do\s+not|don['\u2019]t|always|required|forbidden)\b",
    re.I,
)
# Triggers identify a constraint; retention is judged on the content words.
_CONSTRAINT_STOP = frozenset(
    {
        "the",
        "and",
        "for",
        "with",
        "that",
        "this",
        "from",
        "will",
        "use",
        "not",
        "must",
        "never",
        "always",
        "required",
        "forbidden",
        "dont",
        "don",
        "does",
        "did",
        "are",
        "was",
        "were",
        "you",
        "your",
        "our",
        "but",
        "any",
        "all",
        "can",
        "may",
        "into",
        "onto",
        "than",
        "then",
        "have",
        "has",
        "had",
        "been",
        "being",
        "its",
        "also",
        "only",
        "just",
        "please",
    }
)
_CONSTRAINT_LIMIT = 20
_CONSTRAINT_CLIP = 240


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


def _clip_constraint(text: str) -> str:
    text = re.sub(r"\s+", " ", (text or "").strip())
    if len(text) <= _CONSTRAINT_CLIP:
        return text
    return text[:_CONSTRAINT_CLIP].rstrip()


def _constraint_key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _constraint_chunks(content: str) -> list[str]:
    chunks: list[str] = []
    for line in content.splitlines():
        line = line.strip()
        if not line:
            continue
        for part in re.split(r"(?<=[.!?])\s+|(?<=;)\s+", line):
            part = re.sub(r"^(?:[-*•]+|\d+[.)])\s+", "", part).strip()
            if part:
                chunks.append(part)
    return chunks


def _extract_binding_constraints(messages: list[dict[str, Any]] | None) -> list[str]:
    """Sentence or line snippets that bind the owner (user and assistant only)."""
    found: list[str] = []
    seen: set[str] = set()
    for m in messages or []:
        if m.get("role") not in ("user", "assistant"):
            continue
        content = m.get("content")
        if not isinstance(content, str) or not content.strip():
            continue
        for chunk in _constraint_chunks(content):
            if not _CONSTRAINT_RE.search(chunk):
                continue
            clipped = _clip_constraint(chunk)
            key = _constraint_key(clipped)
            if len(key) < 3 or key in seen:
                continue
            seen.add(key)
            found.append(clipped)
            if len(found) >= _CONSTRAINT_LIMIT:
                return found
    return found


def _constraint_kept(snippet: str, brief_flat: str) -> bool:
    """Kept when the phrase, or enough of its content words, is in the brief."""
    phrase = _constraint_key(snippet)
    if phrase and phrase in brief_flat:
        return True
    words = [w for w in phrase.split() if len(w) >= 3 and w not in _CONSTRAINT_STOP][:8]
    if not words:
        return False
    need = 1 if len(words) == 1 else max(2, (len(words) + 1) // 2)
    hits = 0
    for w in words:
        if re.search(rf"(?<![a-z0-9]){re.escape(w)}(?![a-z0-9])", brief_flat):
            hits += 1
    return hits >= need


def extract_fact_candidates(messages: list[dict[str, Any]] | None) -> dict[str, list[str]]:
    """Pull paths, decisions, and binding constraints (pre-compress ground truth)."""
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
    return {
        "paths": paths[:40],
        "decisions": decisions[:20],
        "constraints": _extract_binding_constraints(messages),
    }


def review_compress_quality(
    *,
    messages_before: list[dict[str, Any]] | None,
    brief: SessionBrief | None,
    tokens_before: int = 0,
    tokens_after: int = 0,
) -> dict[str, Any]:
    """Score whether brief retained paths, decisions, and binding constraints.

    Returns score in [0, 1], lists of kept/lost facts, and human summary.
    With zero constraints the 0.65/0.35 score and ok rule are unchanged.
    One lost binding constraint forces ok False.
    """
    facts = extract_fact_candidates(messages_before)
    paths = facts["paths"]
    decisions = facts["decisions"]
    constraints = facts["constraints"]

    brief_blob = ""
    if brief is not None:
        # Raw user_constraints: the context block can truncate before them.
        brief_blob = (
            brief_to_context_block(brief, max_chars=4000)
            + " "
            + " ".join(brief.artifacts or [])
            + " "
            + " ".join(brief.decisions or [])
            + " "
            + " ".join(brief.key_paths or [])
            + " "
            + " ".join(brief.user_constraints or [])
        )
    brief_l = brief_blob.lower().replace("\\", "/")
    brief_flat = re.sub(r"[^a-z0-9]+", " ", brief_l).strip()

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

    con_kept: list[str] = []
    con_lost: list[str] = []
    for c in constraints:
        if _constraint_kept(c, brief_flat):
            con_kept.append(c)
        else:
            con_lost.append(c)

    n_path = len(paths) or 0
    n_dec = len(decisions) or 0
    n_con = len(constraints) or 0
    path_score = (len(paths_kept) / n_path) if n_path else 1.0
    dec_score = (len(dec_kept) / n_dec) if n_dec else 1.0
    con_score = (len(con_kept) / n_con) if n_con else 1.0
    # Fail-closed when nothing extractable: do not authorize middle-history drop.
    # (Previously defaulted to 0.85, which always passed the 0.65 gate.)
    # Constraints, when present, take a quarter without dropping path weight
    # below half. Zero constraints stay on the historical 0.65/0.35 blend.
    if not (n_path or n_dec or n_con):
        score = 0.40
    elif n_con:
        score = 0.50 * path_score + 0.25 * dec_score + 0.25 * con_score
    else:
        score = 0.65 * path_score + 0.35 * dec_score

    brief_substance = False
    if brief is not None:
        brief_substance = bool(
            (getattr(brief, "intent", None) or "").strip()
            or (getattr(brief, "artifacts", None) or [])
            or (getattr(brief, "decisions", None) or [])
            or (getattr(brief, "decision_records", None) or [])
            or (getattr(brief, "history_thread", None) or [])
            or (getattr(brief, "key_paths", None) or [])
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
    if n_con:
        summary_parts.append(f"constraints {len(con_kept)}/{n_con}")
    if tokens_before:
        summary_parts.append(
            f"tokens {tokens_before}→{tokens_after} ({reduction*100:.0f}% less est.)"
        )
    if not (n_path or n_dec or n_con):
        summary_parts.append("no extractable facts (fail-closed)")

    path_loss_ok = len(paths_lost) <= max(2, n_path // 3) if n_path else True
    # not con_lost is true when nothing was extracted, so the old ok rule holds.
    ok = (
        score >= 0.55
        and path_loss_ok
        and brief_substance
        and (n_path + n_dec) > 0
        and not con_lost
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
        "constraints_total": n_con,
        "constraints_kept": len(con_kept),
        "constraints_lost": len(con_lost),
        "constraints_lost_sample": con_lost[:5],
        "token_reduction_pct": round(reduction * 100, 1),
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "brief_substance": brief_substance,
        "summary": ", ".join(summary_parts) or "no extractable facts",
        "ok": ok,
    }
