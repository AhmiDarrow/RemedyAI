"""Text completions against the shared local llama-server (same Qwen as vision).

Used by nano Router (and later Helper). Never grants tools — text only.
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


def local_text_complete(
    prompt: str,
    *,
    base_url: str,
    max_tokens: int = 16,
    temperature: float = 0.0,
    timeout_s: float = 20.0,
    system: str | None = None,
) -> dict[str, Any]:
    """OpenAI-compat chat completion; returns {ok, text, error}."""
    base = (base_url or "").rstrip("/")
    if not base:
        return {"ok": False, "text": "", "error": "no base_url"}
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    body = {
        "model": "local-qwen",
        "temperature": temperature,
        "max_tokens": max(1, int(max_tokens)),
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
        with urlopen(req, timeout=timeout_s) as resp:
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
        return decode_image(
            p.get("path") or "",
            base_url=str(p.get("base_url") or ""),
            timeout_s=float(p.get("timeout_s") or 90),
            max_image_bytes=int(p.get("max_image_bytes") or 4 * 1024 * 1024),
            extra_question=p.get("extra_question"),
        )

    def _nano_classify(job: LocalJob) -> Any:
        p = job.payload or {}
        prompt = str(p.get("prompt") or "")
        return local_text_complete(
            prompt,
            base_url=str(p.get("base_url") or ""),
            max_tokens=int(p.get("max_tokens") or 8),
            temperature=0.0,
            timeout_s=float(p.get("timeout_s") or 15),
            system=str(
                p.get("system")
                or "Reply with exactly one label word. No punctuation."
            ),
        )

    def _brief_update(job: LocalJob) -> Any:
        """Session Brief refresh on local Qwen (Memory Harness background)."""
        from remedy.memory.harness.local_brief import process_brief_update_job

        return process_brief_update_job(job)

    def _text_job(job: LocalJob) -> Any:
        """Generic local text complete (spread_plan, worker_summarize)."""
        p = job.payload or {}
        prompt = str(p.get("prompt") or "")
        return local_text_complete(
            prompt,
            base_url=str(p.get("base_url") or ""),
            max_tokens=int(p.get("max_tokens") or 64),
            temperature=float(p.get("temperature") or 0.0),
            timeout_s=float(p.get("timeout_s") or 20),
            system=str(p.get("system") or "") or None,
        )

    q.register("vision_decode", _vision_decode)
    q.register("nano_classify", _nano_classify)
    q.register("brief_update", _brief_update)
    q.register("spread_plan", _text_job)
    q.register("worker_summarize", _text_job)
    _handlers_ready = True
