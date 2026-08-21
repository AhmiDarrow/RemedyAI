"""One INFO line per LLM request on logger ``remedy.llm`` — no content, ever.

Until now a provider call left no trace in the logs: a 99 s stall, a burst of
``ConnectionResetError`` at step boundaries, a provider silently returning
``finish_reason=length`` — none of it was attributable to a request. This
module is the single place that formats the line so every call path (the
non-streaming ``post_chat``, the streaming ReAct loop) reports the same
fields in the same order:

    llm provider=deepseek model=deepseek-chat session=abc123 step=2
    status=ok latency_ms=1834 finish=tool_calls tool_calls=1
    prompt_tokens=812 completion_tokens=96 cache_hit_tokens=640

``status`` is one of ``ok`` | ``http_<code>`` | ``aborted`` | ``error``;
``error`` carries the exception type name only.
"""

from __future__ import annotations

import contextlib
import logging
from typing import Any

logger = logging.getLogger("remedy.llm")

_USAGE_KEYS = (
    "prompt_tokens",
    "completion_tokens",
    "total_tokens",
    "cache_hit_tokens",
    "reasoning_tokens",
)


def _short_session(session_id: Any) -> str:
    s = str(session_id or "").strip()
    return s[:12] if s else "-"


def usage_fields(usage: Any) -> dict[str, int]:
    """Pick the integer token counters out of a provider ``usage`` dict.

    Accepts OpenAI (``prompt_tokens``), Anthropic (``input_tokens`` /
    ``output_tokens``) and DeepSeek cache (``prompt_cache_hit_tokens``) shapes.
    """
    if not isinstance(usage, dict):
        return {}
    out: dict[str, int] = {}

    def _take(dst: str, *srcs: str) -> None:
        for k in srcs:
            v = usage.get(k)
            if v is None:
                continue
            try:
                out[dst] = int(v)
                return
            except (TypeError, ValueError):
                continue

    _take("prompt_tokens", "prompt_tokens", "input_tokens")
    _take("completion_tokens", "completion_tokens", "output_tokens")
    _take("total_tokens", "total_tokens")
    _take("cache_hit_tokens", "cache_hit_tokens", "prompt_cache_hit_tokens", "cache_read_input_tokens")
    details = usage.get("completion_tokens_details")
    if isinstance(details, dict) and details.get("reasoning_tokens") is not None:
        with contextlib.suppress(TypeError, ValueError):
            out["reasoning_tokens"] = int(details["reasoning_tokens"])
    return out


def format_llm_line(
    *,
    provider: Any,
    model: Any,
    session_id: Any = None,
    step: Any = None,
    latency_ms: float | None = None,
    status: str = "ok",
    finish_reason: Any = None,
    tool_calls: int | None = None,
    usage: Any = None,
    error: BaseException | str | None = None,
) -> str:
    parts = [
        "llm",
        f"provider={str(provider or '-').strip() or '-'}",
        f"model={str(model or '-').strip() or '-'}",
        f"session={_short_session(session_id)}",
        f"step={step if step is not None else '-'}",
        f"status={status or 'ok'}",
        f"latency_ms={int(latency_ms) if latency_ms is not None else '-'}",
        f"finish={finish_reason or '-'}",
        f"tool_calls={int(tool_calls) if tool_calls is not None else 0}",
    ]
    for k in _USAGE_KEYS:
        v = usage_fields(usage).get(k)
        if v is not None:
            parts.append(f"{k}={v}")
    if error is not None:
        name = error if isinstance(error, str) else type(error).__name__
        parts.append(f"error={name}")
    return " ".join(parts)


def log_llm_call(**fields: Any) -> None:
    """Emit the request line at INFO (aborts/errors at WARNING). Never raises."""
    try:
        line = format_llm_line(**fields)
        status = str(fields.get("status") or "ok")
        if status == "ok":
            logger.info(line)
        else:
            logger.warning(line)
    except Exception:  # pragma: no cover - logging must never break a turn
        logger.debug("llm log line failed", exc_info=True)


def count_tool_calls(response_json: Any, adapter: Any = None) -> int:
    """Tool-call count from a parsed provider response (0 when none)."""
    try:
        if adapter is not None:
            parsed = adapter.extract_response(response_json)
            tc = parsed.get("tool_calls") if isinstance(parsed, dict) else None
            return len(tc) if isinstance(tc, list) else 0
    except Exception:
        pass
    try:
        msg = (response_json.get("choices") or [{}])[0].get("message") or {}
        tc = msg.get("tool_calls")
        return len(tc) if isinstance(tc, list) else 0
    except Exception:
        return 0
