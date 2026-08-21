"""Background local-model Session Brief updates (parallel with cloud provider).

Uses shared llama-server via LocalJobQueue — never blocks the provider turn.
"""

from __future__ import annotations

import contextlib
import json
import logging
import re
import threading
from typing import Any

from remedy.home import default_home

logger = logging.getLogger(__name__)

_JSON_RE = re.compile(r"\{[\s\S]*\}")

# session_id → SessionBrief (process-local; apply path is thread-safe)
_brief_registry: dict[str, Any] = {}
_brief_registry_lock = threading.Lock()


def register_session_brief(session_id: str, brief: Any) -> None:
    """Register brief so background jobs can apply without late-bound closures."""
    sid = (session_id or "").strip()
    if not sid or brief is None:
        return
    with _brief_registry_lock:
        _brief_registry[sid] = brief


def get_registered_brief(session_id: str) -> Any | None:
    sid = (session_id or "").strip()
    if not sid:
        return None
    with _brief_registry_lock:
        return _brief_registry.get(sid)


def _local_base_url() -> str:
    """Prefer RMB chat host when exclusive local agent; else vision/nano stack."""
    try:
        from remedy.interfaces.config import load_config

        cfg = load_config() or {}
        from remedy.runtime.rmb.mode import (
            is_local_agent_mode,
            rmb_chat_base_url,
            rmb_server_running,
            should_skip_vision_stack,
        )

        if is_local_agent_mode(cfg) or should_skip_vision_stack(cfg) or rmb_server_running(
            cfg.get("home_dir") if isinstance(cfg, dict) else None
        ):
            return rmb_chat_base_url(cfg)
    except Exception:
        pass
    try:
        from remedy.interfaces.config import load_config
        from remedy.vision.config import load_vision_json, vision_section_from_config

        cfg = load_config() or {}
        home = cfg.get("home_dir")
        from pathlib import Path

        h = Path(home).expanduser() if home else default_home()
        side = load_vision_json(h)
        v = vision_section_from_config(cfg)
        return str(side.get("base_url") or v.get("base_url") or "http://127.0.0.1:8740/v1")
    except Exception:
        return "http://127.0.0.1:8740/v1"


def _messages_excerpt(messages: list[dict[str, Any]], *, max_chars: int = 6000) -> str:
    parts: list[str] = []
    used = 0
    for m in messages[-24:]:
        role = m.get("role") or "?"
        c = m.get("content")
        if not isinstance(c, str):
            try:
                c = json.dumps(c, default=str)[:400]
            except Exception:
                c = str(c)[:400]
        line = f"{role}: {c[:800]}"
        if used + len(line) > max_chars:
            break
        parts.append(line)
        used += len(line)
    return "\n".join(parts)


def _parse_brief_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    raw = text.strip()
    if "```" in raw:
        raw = re.sub(r"```(?:json)?", "", raw).replace("```", "").strip()
    m = _JSON_RE.search(raw)
    if not m:
        return None
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def apply_local_brief_payload(brief: Any, data: dict[str, Any]) -> None:
    """Merge local model JSON into SessionBrief (mutates brief)."""
    if brief is None or not data:
        return
    try:

        intent = data.get("intent")
        decisions = data.get("decisions")
        if isinstance(decisions, list) and decisions and isinstance(decisions[0], dict):
            brief.merge_summary(
                intent=str(intent or "") or None,
                decision_records=decisions,
                open_tasks=data.get("open_tasks")
                if isinstance(data.get("open_tasks"), list)
                else None,
                next_steps=data.get("next_steps")
                if isinstance(data.get("next_steps"), list)
                else None,
                blockers=data.get("blockers")
                if isinstance(data.get("blockers"), list)
                else None,
                notes=str(data.get("notes") or "") or None,
                history_summary=str(data.get("history_summary") or data.get("summary") or "")
                or None,
            )
        else:
            brief.merge_summary(
                intent=str(intent or "") or None,
                decisions=[str(x) for x in (decisions or [])]
                if isinstance(decisions, list)
                else None,
                open_tasks=data.get("open_tasks")
                if isinstance(data.get("open_tasks"), list)
                else None,
                next_steps=data.get("next_steps")
                if isinstance(data.get("next_steps"), list)
                else None,
                blockers=data.get("blockers")
                if isinstance(data.get("blockers"), list)
                else None,
                notes=str(data.get("notes") or "") or None,
                history_summary=str(data.get("history_summary") or data.get("summary") or "")
                or None,
            )
        for p in data.get("paths") or data.get("artifacts") or []:
            if isinstance(p, str):
                brief.add_artifact(p)
    except Exception as e:
        logger.debug("apply_local_brief_payload failed: %s", e)


def run_local_brief_update_sync(
    messages: list[dict[str, Any]],
    *,
    intent_hint: str = "",
    base_url: str | None = None,
    timeout_s: float = 25.0,
) -> dict[str, Any] | None:
    """Blocking local completion — only for job queue worker."""
    from remedy.runtime.local_infer import local_text_complete

    excerpt = _messages_excerpt(messages)
    if len(excerpt) < 40:
        return None
    prompt = (
        "Update the Session Brief for a coding agent. Return JSON only:\n"
        '{"intent":"...","decisions":[{"decision":"...","why":"...","rejected":"..."}],'
        '"open_tasks":["..."],"next_steps":["..."],"blockers":["..."],'
        '"paths":["..."],"history_summary":"3-5 sentences of this segment",'
        '"notes":"..."}\n'
        "Preserve file paths and technical specifics. Do not invent files.\n"
        f"User focus: {(intent_hint or '')[:300]}\n\n"
        f"Recent messages:\n{excerpt}"
    )
    res = local_text_complete(
        prompt,
        base_url=base_url or _local_base_url(),
        max_tokens=512,
        temperature=0.1,
        timeout_s=timeout_s,
        system=(
            "You maintain working memory for a software agent. "
            "Output valid JSON only. Accuracy of paths and decisions matters."
        ),
    )
    if not res.get("ok"):
        return None
    return _parse_brief_json(str(res.get("text") or ""))


def process_brief_update_job(job: Any) -> Any:
    """Stable queue handler: run local complete + apply to registered session brief."""
    p = getattr(job, "payload", None) or {}
    data = run_local_brief_update_sync(
        list(p.get("messages") or []),
        intent_hint=str(p.get("intent_hint") or ""),
        base_url=str(p.get("base_url") or "") or None,
        timeout_s=float(p.get("timeout_s") or 25),
    )
    sid = str(p.get("session_id") or "")
    brief = get_registered_brief(sid)
    # Fallback: payload may carry a direct weak target key from same-process schedule
    if brief is None and p.get("_brief_ref_id"):
        with _brief_registry_lock:
            brief = _brief_registry.get(str(p.get("_brief_ref_id")))
    if data and brief is not None:
        with _brief_registry_lock:
            apply_local_brief_payload(brief, data)
            try:
                from remedy.memory.harness.quality import review_compress_quality

                qres = review_compress_quality(
                    messages_before=list(p.get("messages") or []),
                    brief=brief,
                )
                score = qres.get("score")
                if score is not None:
                    brief.last_quality_score = float(score)
            except Exception:
                pass
    return data


def schedule_background_brief_update(
    runtime: Any,
    messages: list[dict[str, Any]],
    *,
    intent_hint: str = "",
    level: str = "soft",
) -> bool:
    """Fire-and-forget local brief job. Returns True if queued or updated.

    On RMB / local agent: **heuristic only** — never schedule a second inference
    on the same llama-server as chat (avoids GPU queue thrash).
    """
    try:
        brief = getattr(runtime, "_session_brief", None)
        if brief is None:
            return False

        # Exclusive/local chat host: keep Session Brief alive without contending
        try:
            from remedy.runtime.rmb.mode import (
                is_local_agent_mode,
                silent_context_for_local_agent,
            )

            prov = str(getattr(runtime, "_llm_provider", "") or "")
            base = str(getattr(runtime, "_llm_base_url", "") or "")
            cfg = {
                "llm_provider": prov,
                "llm_base_url": base,
            }
            if is_local_agent_mode(cfg) or silent_context_for_local_agent(
                cfg, provider=prov, base_url=base
            ):
                from remedy.memory.harness.compressor import heuristic_merge_from_history

                heuristic_merge_from_history(
                    brief, list(messages or []), intent_hint=intent_hint
                )
                logger.debug(
                    "RMB/local: heuristic brief update (no local infer job) level=%s",
                    level,
                )
                return True
        except Exception:
            pass

        from remedy.runtime.jobs import LocalJob, default_queue
        from remedy.runtime.local_infer import ensure_handlers_registered
        from remedy.runtime.roles import LocalRole as LR

        ensure_handlers_registered()
        q = default_queue()
        st = q.status()
        pending = st.get("pending") or []
        if len(pending) > 2:
            return False

        sid = str(
            getattr(brief, "session_id", None)
            or getattr(runtime, "_session_id", None)
            or ""
        )
        if not sid:
            sid = f"anon-{id(brief)}"
            with contextlib.suppress(Exception):
                brief.session_id = sid
        register_session_brief(sid, brief)

        job = LocalJob(
            role=LR.HELPER,
            kind="brief_update",
            payload={
                "messages": list(messages[-30:]),
                "intent_hint": intent_hint,
                "base_url": _local_base_url(),
                "level": level,
                "session_id": sid,
            },
            priority=1 if level == "strong" else 0,
        )
        q.submit(job, wait=False)
        logger.debug(
            "Queued local brief_update job %s level=%s sid=%s",
            job.job_id,
            level,
            sid,
        )
        return True
    except Exception as e:
        logger.debug("schedule_background_brief_update failed: %s", e)
        return False
