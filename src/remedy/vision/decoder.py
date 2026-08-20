"""Call local llama-server to turn images into structured text briefs."""

from __future__ import annotations

import base64
import contextlib
import json
import logging
import mimetypes
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request

from remedy.vision.prompts import DECODE_SYSTEM, decode_user_prompt
from remedy.vision.runtime import mark_used

logger = logging.getLogger(__name__)


def _guess_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(path.name)
    if mime and mime.startswith("image/"):
        return mime
    return "image/png"


def _image_data_url(path: Path, max_bytes: int) -> str | None:
    if not path.is_file():
        return None
    raw = path.read_bytes()
    if len(raw) > max_bytes:
        # Still try — many VLMs accept larger; caller may have resized
        if len(raw) > max_bytes * 3:
            return None
    b64 = base64.standard_b64encode(raw).decode("ascii")
    mime = _guess_mime(path)
    return f"data:{mime};base64,{b64}"


def decode_image(
    path: str | Path,
    *,
    base_url: str,
    timeout_s: float = 90.0,
    max_image_bytes: int = 4 * 1024 * 1024,
    extra_question: str | None = None,
    model: str = "vision-decoder",
) -> dict[str, Any]:
    """Decode one image file to a text brief via OpenAI-compat chat completions."""
    from remedy.core.security import is_loopback_service_url, urlopen_no_redirect

    p = Path(path)
    data_url = _image_data_url(p, max_image_bytes)
    if not data_url:
        return {
            "ok": False,
            "path": str(p),
            "error": "Image missing or too large for decode payload",
            "text": "",
        }

    base = (base_url or "").rstrip("/")
    if not base or not is_loopback_service_url(base):
        return {
            "ok": False,
            "path": str(p),
            "error": "vision base_url must be loopback",
            "text": "",
        }

    body = {
        "model": model,
        "temperature": 0.2,
        "max_tokens": 1200,
        "messages": [
            {"role": "system", "content": DECODE_SYSTEM},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": decode_user_prompt(p.name, extra_question)},
                    {"type": "image_url", "image_url": {"url": data_url}},
                ],
            },
        ],
    }
    url = base + "/chat/completions"
    if not url.endswith("/chat/completions"):
        # base_url already ends with /v1
        pass
    req = Request(
        url,
        data=json.dumps(body).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "User-Agent": "RemedyAI-vision/1.0",
        },
        method="POST",
    )
    import time as _time

    t0 = _time.perf_counter()
    try:
        # Never follow Location off-loopback (SSRF via poisoned base or 3xx).
        with urlopen_no_redirect(req, timeout=timeout_s) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        err_body = ""
        with contextlib.suppress(Exception):
            err_body = e.read().decode("utf-8", errors="replace")[:500]
        _record_decode_metric(ok=False, seconds=_time.perf_counter() - t0)
        return {
            "ok": False,
            "path": str(p),
            "error": f"HTTP {e.code}: {err_body or e.reason}",
            "text": "",
        }
    except (URLError, TimeoutError, OSError, json.JSONDecodeError) as e:
        _record_decode_metric(ok=False, seconds=_time.perf_counter() - t0)
        return {"ok": False, "path": str(p), "error": str(e), "text": ""}

    mark_used()
    try:
        choice = (payload.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        content = msg.get("content")
        if isinstance(content, list):
            parts = []
            for c in content:
                if isinstance(c, dict) and c.get("type") == "text":
                    parts.append(str(c.get("text") or ""))
                elif isinstance(c, str):
                    parts.append(c)
            text = "\n".join(parts).strip()
        else:
            text = str(content or "").strip()
    except Exception as e:
        _record_decode_metric(ok=False, seconds=_time.perf_counter() - t0)
        return {"ok": False, "path": str(p), "error": f"Bad response shape: {e}", "text": ""}

    if not text:
        _record_decode_metric(ok=False, seconds=_time.perf_counter() - t0)
        return {"ok": False, "path": str(p), "error": "Empty decoder response", "text": ""}

    _record_decode_metric(ok=True, seconds=_time.perf_counter() - t0)
    return {"ok": True, "path": str(p), "text": text, "error": None}


def _record_decode_metric(*, ok: bool, seconds: float) -> None:
    try:
        from remedy.core.metrics import default_registry

        default_registry.counter(
            "remedy_vision_decode_total",
            result="ok" if ok else "error",
        ).inc()
        default_registry.histogram("remedy_vision_decode_seconds").observe(max(0.0, seconds))
    except Exception:
        pass


def decode_images(
    paths: Sequence[str | Path],
    *,
    base_url: str,
    timeout_s: float = 90.0,
    max_image_bytes: int = 4 * 1024 * 1024,
    extra_question: str | None = None,
) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for path in paths:
        results.append(
            decode_image(
                path,
                base_url=base_url,
                timeout_s=timeout_s,
                max_image_bytes=max_image_bytes,
                extra_question=extra_question,
            )
        )
    return results
