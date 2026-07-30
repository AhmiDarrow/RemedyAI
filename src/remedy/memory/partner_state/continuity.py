"""Phase E — Continuity Core: async/local maintenance of Partner State."""

from __future__ import annotations

import logging
from contextlib import suppress
from typing import Any

logger = logging.getLogger(__name__)


def process_continuity_core_job(job: Any) -> Any:
    """LocalJob handler: maintain graph + brief without blocking the frontier."""
    p = getattr(job, "payload", None) or {}
    sid = str(p.get("session_id") or "")
    from remedy.memory.partner_state.state import PartnerState, _registry, _registry_lock

    brief = None
    st = None
    with _registry_lock:
        st = _registry.get(sid) if sid else None
    if st is None:
        # Reconstruct from payload home if needed
        home = p.get("home")
        if sid:
            st = PartnerState(session_id=sid, home=home)
            st.load()
            with _registry_lock:
                _registry[sid] = st

    # Optional brief from registry
    with suppress(Exception):
        from remedy.memory.harness.local_brief import get_registered_brief

        brief = get_registered_brief(sid)

    if st is None:
        return {"ok": False, "error": "no partner state"}

    # Optional local model enrichment of decisions from recent txn previews
    enrich = None
    if p.get("use_local"):
        with suppress(Exception):
            enrich = _local_enrich_decisions(st, p)

    tick = st.continuity_tick(brief=brief)
    if enrich:
        tick["local_enrich"] = enrich
    # Also run brief merge quality path if we have brief
    if brief is not None:
        with suppress(Exception):
            st.apply_graph_to_brief(brief)
    return {"ok": True, **tick}


def _local_enrich_decisions(st: Any, payload: dict[str, Any]) -> dict[str, Any] | None:
    from remedy.runtime.local_infer import local_text_complete

    txns = list(getattr(st, "tool_txns", []) or [])[-12:]
    if len(txns) < 2:
        return None
    lines = []
    for t in txns:
        lines.append(
            f"{t.name} {t.outcome}: {(t.claim or t.result_preview or '')[:120]}"
        )
    prompt = (
        "Extract 0-4 durable decisions or facts for a coding agent. JSON only:\n"
        '{"items":[{"kind":"decision|fact|commitment","text":"...","why":"..."}]}\n'
        "Recent tools:\n" + "\n".join(lines)
    )
    base = str(payload.get("base_url") or "") or None
    res = local_text_complete(
        prompt,
        base_url=base,
        max_tokens=256,
        temperature=0.1,
        timeout_s=float(payload.get("timeout_s") or 20),
        system="You maintain agent working memory. JSON only. Do not invent file paths.",
    )
    if not res.get("ok"):
        return None
    text = str(res.get("text") or "")
    import json
    import re

    m = re.search(r"\{[\s\S]*\}", text)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    items = data.get("items") if isinstance(data, dict) else None
    if not isinstance(items, list):
        return None
    n = 0
    for raw in items[:6]:
        if not isinstance(raw, dict):
            continue
        kind = str(raw.get("kind") or "fact")
        if kind not in ("decision", "fact", "commitment", "hypothesis"):
            kind = "fact"
        t = str(raw.get("text") or "").strip()
        if not t:
            continue
        st.add_node(
            kind=kind,
            text=t,
            why=str(raw.get("why") or ""),
            source="local_core",
            confidence=0.72,
        )
        n += 1
    return {"added": n}


def schedule_continuity_core(
    runtime: Any,
    *,
    use_local: bool = False,
    priority: int = 0,
) -> bool:
    """Fire-and-forget Continuity Core job. Never blocks the provider turn."""
    try:
        from remedy.memory.partner_state.state import ensure_partner_state
        from remedy.runtime.jobs import LocalJob, default_queue
        from remedy.runtime.local_infer import ensure_handlers_registered
        from remedy.runtime.roles import LocalRole as LR

        st = ensure_partner_state(runtime)
        # Always do a cheap in-process tick immediately (no queue required)
        brief = getattr(runtime, "_session_brief", None)
        st.continuity_tick(brief=brief)

        ensure_handlers_registered()
        q = default_queue()
        st_status = q.status()
        pending = st_status.get("pending") or []
        if len(pending) > 3:
            return False

        sid = st.session_id
        home = None
        with suppress(Exception):
            home = str(getattr(getattr(runtime, "config", None), "home_dir", None) or "") or None

        base_url = None
        if use_local:
            with suppress(Exception):
                from remedy.memory.harness.local_brief import _local_base_url

                base_url = _local_base_url()

        # Register brief for core job
        with suppress(Exception):
            from remedy.memory.harness.local_brief import register_session_brief

            if brief is not None:
                register_session_brief(sid, brief)

        job = LocalJob(
            role=LR.HELPER,
            kind="continuity_core",
            payload={
                "session_id": sid,
                "home": home,
                "use_local": bool(use_local),
                "base_url": base_url,
                "timeout_s": 22,
            },
            priority=priority,
        )
        q.submit(job, wait=False)
        logger.debug("Queued continuity_core job %s sid=%s", job.job_id, sid)
        return True
    except Exception as e:
        logger.debug("schedule_continuity_core failed: %s", e)
        # Still try in-process tick
        with suppress(Exception):
            from remedy.memory.partner_state.state import ensure_partner_state

            ensure_partner_state(runtime).continuity_tick(
                brief=getattr(runtime, "_session_brief", None)
            )
        return False
