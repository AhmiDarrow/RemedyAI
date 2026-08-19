"""Text completions against the MDL tiered local llama-server cluster.

Used by nano Router (and later Helper). Never grants tools — text only.
MDL (Machine Density Language) routes tasks to appropriate model depth tiers.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

logger = logging.getLogger(__name__)


# Hard caps so a runaway ranker/router cannot flood llama-server with multi-MB prompts.
_MAX_PROMPT_CHARS = 12_000
_MAX_SYSTEM_CHARS = 2_000


def local_text_complete(
    prompt: str,
    *,
    # Callers legitimately pass None (no local endpoint configured yet) and
    # the body already answers {'ok': False, 'error': 'no base_url'}.
    base_url: str | None = None,
    max_tokens: int = 16,
    temperature: float = 0.0,
    timeout_s: float = 20.0,
    system: str | None = None,
) -> dict[str, Any]:
    """OpenAI-compat chat completion; returns {ok, text, error}."""
    from remedy.core.security import is_loopback_service_url, urlopen_no_redirect

    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "text": "", "error": "no base_url"}
    # Local nano/helper only — refuse off-loopback base (poisoned vision.json / jobs).
    if not is_loopback_service_url(base):
        return {"ok": False, "text": "", "error": "local base_url must be loopback"}
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    # Truncate (do not refuse) so classify/rerank still get a usable prefix.
    prompt_s = str(prompt or "")
    if len(prompt_s) > _MAX_PROMPT_CHARS:
        prompt_s = prompt_s[:_MAX_PROMPT_CHARS] + "\n…[truncated]"
    system_s = str(system).strip() if system else ""
    if system_s and len(system_s) > _MAX_SYSTEM_CHARS:
        system_s = system_s[:_MAX_SYSTEM_CHARS] + "…"
    messages: list[dict[str, str]] = []
    if system_s:
        messages.append({"role": "system", "content": system_s})
    messages.append({"role": "user", "content": prompt_s})
    body = {
        "model": "local-qwen",
        "temperature": temperature,
        "max_tokens": max(1, min(512, int(max_tokens or 16))),
        "messages": messages,
    }
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "RemedyAI-nanoswarm/1.0",
        },
        method="POST",
    )
    try:
        # Never follow Location (loopback 302 → metadata/LAN SSRF).
        with urlopen_no_redirect(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        err = ""
        try:
            err = e.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            err = str(e.reason)
        return {"ok": False, "text": "", "error": f"HTTP {e.code}: {err}"}
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        return {"ok": False, "text": "", "error": str(e)}

    try:
        from remedy.runtime.mdl_runtime import mark_tier_used

        mark_tier_used("full")
    except Exception:
        try:
            from remedy.vision.runtime import mark_used

            mark_used()
        except Exception:
            pass

    try:
        choice = (payload.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            text = " ".join(
                str(c.get("text") or c) if isinstance(c, dict) else str(c) for c in content
            ).strip()
        else:
            text = str(content or "").strip()
    except Exception as e:
        return {"ok": False, "text": "", "error": f"bad response: {e}"}

    return {"ok": bool(text), "text": text, "error": None if text else "empty"}


_handlers_ready = False


def _resolve_mdl_url(
    task_kind: str,
    base_url: str = "",
    prompt: str = "",
) -> tuple[str, str]:
    """Resolve the base_url for a task using MDL routing.

    Falls back to the provided base_url (legacy single-server mode) if MDL
    tiers are not configured or not running.

    Returns (base_url, tier_name).
    """
    # RMB owns local inference — never route to SmolVLM tiers
    try:
        from remedy.interfaces.config import load_config
        from remedy.runtime.rmb.mode import is_local_agent_mode, rmb_chat_base_url

        cfg = load_config() or {}
        if is_local_agent_mode(cfg):
            return rmb_chat_base_url(cfg), "rmb"
    except Exception:
        pass
    try:
        from remedy.runtime.mdl import route_task
        from remedy.runtime.mdl_runtime import ensure_tier, is_tier_running

        routing = route_task(task_kind, prompt)
        tier = routing.tier

        if is_tier_running(tier):
            return routing.base_url, tier

        if tier == "full":
            return base_url, "full"

        # Tier not running — try to start it using vision.json state
        try:
            from remedy.vision.config import load_vision_json

            vstate = load_vision_json()
            model_path = vstate.get("model_path")
            mmproj = vstate.get("mmproj_path")
            rb = vstate.get("runtime_binary")

            if not rb or not Path(rb).is_file():
                from remedy.vision.install import runtime_binary_path
                rb_path = runtime_binary_path()
                rb = str(rb_path) if rb_path else None

            if model_path and rb:
                started = ensure_tier(
                    tier,
                    model_path=str(model_path),
                    mmproj_path=str(mmproj) if mmproj else None,
                    runtime_binary=str(rb),
                )
                if started.get("ok"):
                    return routing.base_url, tier
        except Exception:
            logger.debug("MDL tier start failed, falling back", exc_info=True)

        if is_tier_running("light"):
            from remedy.runtime.mdl import get_tier_base_url
            return get_tier_base_url("light"), "light"
    except Exception:
        logger.debug("MDL routing unavailable, using legacy base_url", exc_info=True)

    return base_url, "legacy"


def _ensemble_confidence(result: dict[str, Any]) -> float:
    """Estimate confidence of a classify/label result, 0-1."""
    if not result.get("ok"):
        return 0.0
    text = str(result.get("text") or "").strip()
    if not text:
        return 0.0
    # Short, single-word responses are high confidence
    if 1 <= len(text.split()) <= 3 and len(text) < 30:
        return 0.85
    return 0.3  # long/rambling response = low confidence


def ensure_handlers_registered() -> None:
    """Register vision_decode + nano_classify on the shared job queue (idempotent)."""
    global _handlers_ready
    from remedy.runtime.jobs import LocalJob, default_queue

    q = default_queue()
    if _handlers_ready and "vision_decode" in (q.status().get("handlers") or []):
        return

    def _vision_decode(job: LocalJob) -> Any:
        from remedy.vision.decoder import decode_image

        p = job.payload or {}
        prompt = str(p.get("extra_question") or "")
        url, _ = _resolve_mdl_url("vision_decode", str(p.get("base_url") or ""), prompt)
        return decode_image(
            p.get("path") or "",
            base_url=url,
            timeout_s=float(p.get("timeout_s") or 90),
            max_image_bytes=int(p.get("max_image_bytes") or 4 * 1024 * 1024),
            extra_question=p.get("extra_question"),
        )

    def _nano_classify(job: LocalJob) -> Any:
        p = job.payload or {}
        prompt = str(p.get("prompt") or "")
        base = str(p.get("base_url") or "")

        # Speculative: try LIGHT tier first, escalate if confidence low
        url, tier = _resolve_mdl_url("nano_classify", base, prompt)
        result = local_text_complete(
            prompt,
            base_url=url,
            max_tokens=int(p.get("max_tokens") or 8),
            temperature=0.0,
            timeout_s=float(p.get("timeout_s") or 15),
            system=str(
                p.get("system")
                or "Reply with exactly one label word. No punctuation."
            ),
        )

        if tier == "light":
            confidence = _ensemble_confidence(result)
            if confidence < 0.70 and result.get("ok"):
                logger.debug("Low confidence (%.2f) on LIGHT tier, escalating", confidence)
                from remedy.runtime.mdl import escalate_routing, route_task

                routing = route_task("nano_classify", prompt)
                escalated = escalate_routing(routing)
                # Ensure escalated tier is running
                try:
                    from remedy.runtime.mdl_runtime import ensure_tier
                    from remedy.vision.config import load_vision_json
                    from remedy.vision.install import runtime_binary_path

                    vstate = load_vision_json()
                    model_path = vstate.get("model_path")
                    mmproj = vstate.get("mmproj_path")
                    rb = vstate.get("runtime_binary")

                    if not rb or not Path(rb).is_file():
                        rb_path = runtime_binary_path()
                        rb = str(rb_path) if rb_path else None

                    if model_path and rb:
                        ensure_tier(
                            escalated.tier,
                            model_path=str(model_path),
                            mmproj_path=str(mmproj) if mmproj else None,
                            runtime_binary=str(rb),
                        )
                except Exception:
                    pass  # fall through, return LIGHT result

                if escalated.tier != "light":
                    result = local_text_complete(
                        prompt,
                        base_url=escalated.base_url,
                        max_tokens=int(p.get("max_tokens") or 8),
                        temperature=0.0,
                        timeout_s=float(p.get("timeout_s") or 15),
                        system=str(
                            p.get("system")
                            or "Reply with exactly one label word. No punctuation."
                        ),
                    )

        return result

    def _brief_update(job: LocalJob) -> Any:
        from remedy.runtime.mdl import route_task
        routing = route_task("brief_update")
        # Inject MDL base_url into job payload so process_brief_update_job can use it
        p = job.payload or {}
        p["mdl_base_url"] = routing.base_url
        job.payload = p

        from remedy.memory.harness.local_brief import process_brief_update_job
        return process_brief_update_job(job)

    def _continuity_core(job: LocalJob) -> Any:
        from remedy.runtime.mdl import route_task
        routing = route_task("continuity_core")
        p = job.payload or {}
        p["mdl_base_url"] = routing.base_url
        job.payload = p

        from remedy.memory.partner_state.continuity import process_continuity_core_job
        return process_continuity_core_job(job)

    def _text_job(job: LocalJob) -> Any:
        p = job.payload or {}
        prompt = str(p.get("prompt") or "")
        url, _ = _resolve_mdl_url("text", str(p.get("base_url") or ""), prompt)
        return local_text_complete(
            prompt,
            base_url=url,
            max_tokens=int(p.get("max_tokens") or 64),
            temperature=float(p.get("temperature") or 0.0),
            timeout_s=float(p.get("timeout_s") or 20),
            system=str(p.get("system") or "") or None,
        )

    q.register("vision_decode", _vision_decode)
    q.register("nano_classify", _nano_classify)
    q.register("brief_update", _brief_update)
    q.register("continuity_core", _continuity_core)

    def _library_rerank(job: LocalJob) -> Any:
        p = job.payload or {}
        prompt = str(p.get("prompt") or "")
        url, _ = _resolve_mdl_url("library_rerank", str(p.get("base_url") or ""), prompt)
        return local_text_complete(
            prompt,
            base_url=url,
            max_tokens=int(p.get("max_tokens") or 24),
            temperature=0.0,
            timeout_s=float(p.get("timeout_s") or 12),
            system=str(
                p.get("system")
                or "Reply with one skill id from the list, or NONE. No other text."
            ),
        )

    q.register("spread_plan", _text_job)
    q.register("worker_summarize", _text_job)
    q.register("library_rerank", _library_rerank)
    _handlers_ready = True
