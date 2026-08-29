"""Process-local life-task card the desktop polls (plan, progress, review).

``life_drive`` is a regular tool — it cannot yield SSE mid-step. The owner
surface therefore reads this hub (same pattern as the approval queue) and
optionally a closing ``@@life_task`` marker after the tool returns.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

_lock = threading.Lock()
_by_session: dict[str, dict[str, Any]] = {}
_latest: dict[str, Any] | None = None

_CHOICES_ASK = ["yes", "no", "explain"]
_CHOICES_REVIEW = ["explain"]


def reset() -> None:
    """Drop every card (tests)."""
    global _latest
    with _lock:
        _by_session.clear()
        _latest = None


def publish(payload: dict[str, Any], *, session_id: str | None = None) -> dict[str, Any]:
    """Replace the current card for *session_id* (or ``default``)."""
    global _latest
    sid = str(session_id or payload.get("session_id") or "default").strip() or "default"
    card = dict(payload)
    card["session_id"] = sid
    card["updated_at"] = time.time()
    with _lock:
        _by_session[sid] = card
        _latest = card
    return card


def current(session_id: str | None = None) -> dict[str, Any] | None:
    with _lock:
        if session_id:
            hit = _by_session.get(str(session_id).strip())
            if hit is not None:
                return dict(hit)
            return None
        return dict(_latest) if _latest is not None else None


def clear(session_id: str | None = None) -> None:
    global _latest
    with _lock:
        if session_id:
            _by_session.pop(str(session_id).strip(), None)
            if _latest is not None and str(_latest.get("session_id") or "") == str(
                session_id
            ).strip():
                _latest = None
            return
        _by_session.clear()
        _latest = None


def sse_card(card: dict[str, Any] | None) -> dict[str, Any]:
    """Small payload for ``@@life_task`` / GET current — no giant markdown."""
    if not card:
        return {}
    steps_out: list[dict[str, Any]] = []
    for s in card.get("steps") or []:
        if not isinstance(s, dict):
            continue
        steps_out.append(
            {
                "title": s.get("title") or "",
                "status": s.get("status") or "",
                "observed": str(s.get("observed") or "")[:240],
                "block_reason": s.get("block_reason") or "",
            }
        )
        if len(steps_out) >= 20:
            break
    keys = (
        "task_id",
        "goal",
        "status",
        "ok",
        "spoken",
        "step",
        "total",
        "title",
        "approval_id",
        "choices",
        "checkpoint",
        "kind",
        "session_id",
        "updated_at",
    )
    out = {k: card.get(k) for k in keys if card.get(k) not in (None, "")}
    out["steps"] = steps_out
    if card.get("choices"):
        out["choices"] = list(card.get("choices") or [])
    return out


def life_task_marker(card: dict[str, Any] | None) -> str:
    body = json.dumps(sse_card(card), default=str, separators=(",", ":"), ensure_ascii=False)
    return f"@@life_task:{body}"


def parse_life_task_token(token: str) -> dict[str, Any]:
    raw = token[len("@@life_task:") :] if token.startswith("@@life_task:") else token
    try:
        obj = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        return {}
    return obj if isinstance(obj, dict) else {}


def build_card(
    *,
    goal: str = "",
    status: str = "",
    steps: list[dict[str, Any]] | None = None,
    source_steps: list[dict[str, Any]] | None = None,
    spoken: str = "",
    task_id: str | None = None,
    approval_id: str | None = None,
    kind: str = "",
    ok: bool = False,
    markdown: str = "",
    session_id: str | None = None,
) -> dict[str, Any]:
    """Owner-facing card: one spoken sentence, step N of M, Yes/No/Explain."""
    src = [s for s in (source_steps or []) if isinstance(s, dict)]
    ran = [s for s in (steps or []) if isinstance(s, dict)]
    total = max(len(src), len(ran), 1)
    done_n = sum(1 for s in ran if s.get("status") in ("done", "skipped"))
    title = ""
    for s in ran:
        title = str(s.get("title") or title)
        if s.get("status") not in ("done", "skipped"):
            break
    if not title and src:
        idx = min(done_n, len(src) - 1)
        title = str(src[idx].get("title") or src[idx].get("action") or "")
    st = (status or "").strip() or "blocked"
    step_n = total if st == "done" else min(done_n + 1, total)
    ask = st in {"need_you", "blocked"} or kind == "plan_gate"
    card: dict[str, Any] = {
        "goal": goal,
        "status": st,
        "ok": bool(ok),
        "spoken": spoken,
        "step": step_n,
        "total": total,
        "title": title,
        "steps": ran or [
            {
                "title": str(s.get("title") or s.get("action") or f"step {i}"),
                "status": "pending",
                "observed": "",
                "block_reason": "",
            }
            for i, s in enumerate(src, 1)
        ],
        "source_steps": src,
        "markdown": markdown,
        "choices": list(_CHOICES_ASK if ask else _CHOICES_REVIEW),
        "checkpoint": st == "need_you",
        "kind": kind or ("plan_gate" if kind == "plan_gate" else st),
        "session_id": session_id,
    }
    if task_id:
        card["task_id"] = task_id
    if approval_id:
        card["approval_id"] = approval_id
    return card
