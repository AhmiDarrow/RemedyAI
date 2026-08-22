"""Provider-agnostic request pacing and HTTP 429 backoff for LLM calls.

Two small, independent pieces:

* **Pacing** — some hosted gateways (the free demo gateway is one) accept
  roughly one request per second. A ReAct loop fires the next model round
  the instant a tool result lands, so a multi-step turn trips the limiter on
  round two. A provider's catalog entry may declare
  ``min_request_interval_s``; consecutive calls to that provider are spaced
  at least that far apart (process-wide, per provider id).

* **Retry-After** — on ``429`` the loop waits for the interval the host asked
  for (``Retry-After`` header or ``retry_after`` / ``retry_after_ms`` in the
  JSON body), capped, then retries the *same* request a bounded number of
  times before any recovery / force-answer path runs.

Nothing here touches the network; callers issue the request.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import threading
import time
from email.utils import parsedate_to_datetime
from typing import Any

logger = logging.getLogger(__name__)

#: Upper bound on one Retry-After wait — a host asking for minutes gets the
#: normal provider-error path instead of a turn that looks hung.
RETRY_AFTER_CAP_S = 30.0
#: Wait used when a 429 carries no usable hint.
RETRY_AFTER_DEFAULT_S = 2.0
#: Same-request retries on 429 per model round.
RATE_LIMIT_MAX_RETRIES = 3

_last_request_at: dict[str, float] = {}
_pace_lock = threading.Lock()

_RETRY_AFTER_KEY_RE = re.compile(
    r'"retry[_-]?after(?:_ms|_seconds|_secs)?"\s*:\s*"?(\d+(?:\.\d+)?)"?',
    re.I,
)
_RETRY_IN_TEXT_RE = re.compile(
    r"(?:retry|try again)\s+(?:after|in)\s+(\d+(?:\.\d+)?)\s*(ms|milliseconds?|s|sec|seconds?)?",
    re.I,
)


def min_request_interval_s(provider: str | None) -> float:
    """Seconds consecutive calls to *provider* must be apart (0 = unpaced).

    Read from ``PROVIDER_CATALOG[provider]["min_request_interval_s"]`` when
    present; the catalog is the single place a provider's limits live.
    """
    p = (provider or "").strip().lower()
    if not p:
        return 0.0
    try:
        from remedy.interfaces.provider_catalog import PROVIDER_CATALOG

        meta = PROVIDER_CATALOG.get(p) or {}
        val = float(meta.get("min_request_interval_s") or 0.0)
    except Exception:
        return 0.0
    if val != val or val <= 0:  # NaN / non-positive
        return 0.0
    return min(val, 60.0)


def seconds_until_allowed(
    provider: str | None, *, now: float | None = None
) -> float:
    """How long the next call to *provider* must still wait (0 when free)."""
    interval = min_request_interval_s(provider)
    if interval <= 0:
        return 0.0
    p = (provider or "").strip().lower()
    t = time.monotonic() if now is None else float(now)
    with _pace_lock:
        last = _last_request_at.get(p)
    if last is None:
        return 0.0
    return max(0.0, (last + interval) - t)


def note_request_sent(provider: str | None, *, now: float | None = None) -> None:
    """Record that a request to *provider* just left."""
    p = (provider or "").strip().lower()
    if not p:
        return
    t = time.monotonic() if now is None else float(now)
    with _pace_lock:
        _last_request_at[p] = t


def reset_pacing(provider: str | None = None) -> None:
    """Forget pacing state (tests / provider switch)."""
    with _pace_lock:
        if provider:
            _last_request_at.pop(provider.strip().lower(), None)
        else:
            _last_request_at.clear()


async def sleep_abortable(
    seconds: float, abort_ev: asyncio.Event | None = None
) -> None:
    """Sleep *seconds*; raise ``CancelledError`` as soon as *abort_ev* is set."""
    if seconds <= 0:
        return
    if abort_ev is None:
        await asyncio.sleep(seconds)
        return
    if abort_ev.is_set():
        raise asyncio.CancelledError()
    try:
        await asyncio.wait_for(abort_ev.wait(), timeout=seconds)
    except TimeoutError:
        return
    raise asyncio.CancelledError()


async def pace_before_request(
    provider: str | None, abort_ev: asyncio.Event | None = None
) -> float:
    """Wait so this call honours the provider's minimum interval, then mark it.

    Returns the seconds actually waited. Providers without an interval return
    immediately. Abortable via *abort_ev* (raises ``CancelledError``).
    """
    wait = seconds_until_allowed(provider)
    if wait > 0:
        logger.debug("pacing %s: sleeping %.2fs before next request", provider, wait)
        await sleep_abortable(wait, abort_ev)
    note_request_sent(provider)
    return wait


def _header_get(headers: Any, name: str) -> str | None:
    if headers is None:
        return None
    getter = getattr(headers, "get", None)
    if not callable(getter):
        return None
    for key in (name, name.lower(), name.title(), name.upper()):
        try:
            v = getter(key)
        except Exception:
            v = None
        if v is not None and str(v).strip():
            return str(v).strip()
    return None


def _parse_header_value(raw: str | None) -> float | None:
    if not raw:
        return None
    s = raw.strip()
    try:
        return max(0.0, float(s))
    except ValueError:
        pass
    # HTTP-date form
    try:
        when = parsedate_to_datetime(s)
    except Exception:
        return None
    if when is None:
        return None
    try:
        import datetime as _dt

        if when.tzinfo is None:
            when = when.replace(tzinfo=_dt.UTC)
        delta = (when - _dt.datetime.now(_dt.UTC)).total_seconds()
    except Exception:
        return None
    return max(0.0, delta)


def _walk_json_for_retry_after(obj: Any, depth: int = 0) -> float | None:
    if depth > 4:
        return None
    if isinstance(obj, dict):
        for k, v in obj.items():
            kl = str(k).lower().replace("-", "_")
            if kl in ("retry_after", "retry_after_seconds", "retry_after_secs", "retryafter"):
                try:
                    return max(0.0, float(v))
                except (TypeError, ValueError):
                    continue
            if kl == "retry_after_ms":
                try:
                    return max(0.0, float(v) / 1000.0)
                except (TypeError, ValueError):
                    continue
        for v in obj.values():
            found = _walk_json_for_retry_after(v, depth + 1)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for v in obj[:8]:
            found = _walk_json_for_retry_after(v, depth + 1)
            if found is not None:
                return found
    return None


def retry_after_hint(headers: Any = None, body: str | None = None) -> float | None:
    """Raw seconds the host asked us to wait, or ``None`` when it said nothing.

    Header first (``Retry-After`` seconds or HTTP-date), then
    ``retry_after`` / ``retry_after_ms`` in a JSON body, then a plain-prose
    "try again in N seconds". Uncapped — see :func:`parse_retry_after`.
    """
    val: float | None = _parse_header_value(_header_get(headers, "Retry-After"))
    if val is None:
        val = _parse_header_value(_header_get(headers, "X-RateLimit-Reset-After"))
    if val is None and body:
        text = body.strip()
        if text.startswith("{") or text.startswith("["):
            try:
                val = _walk_json_for_retry_after(json.loads(text))
            except (ValueError, TypeError):
                val = None
        if val is None:
            m = _RETRY_AFTER_KEY_RE.search(text)
            if m:
                try:
                    num = float(m.group(1))
                    val = num / 1000.0 if "_ms" in m.group(0).lower() else num
                except ValueError:
                    val = None
        if val is None:
            m = _RETRY_IN_TEXT_RE.search(text)
            if m:
                try:
                    num = float(m.group(1))
                    unit = (m.group(2) or "s").lower()
                    val = num / 1000.0 if unit.startswith("m") else num
                except ValueError:
                    val = None
    return val


def clamp_wait(seconds: float, *, cap: float = RETRY_AFTER_CAP_S) -> float:
    """Bound one wait: never more than *cap*, and a small floor so an
    over-eager ``retry_after: 0`` does not hammer the host."""
    return max(0.25, min(float(seconds), float(cap)))


def parse_retry_after(
    headers: Any = None,
    body: str | None = None,
    *,
    default: float = RETRY_AFTER_DEFAULT_S,
    cap: float = RETRY_AFTER_CAP_S,
) -> float:
    """Seconds a 429/503 asked us to wait — *default* when it said nothing.

    Always returns a usable, clamped number (see :func:`clamp_wait`).
    """
    val = retry_after_hint(headers, body)
    if val is None:
        val = float(default)
    return clamp_wait(val, cap=cap)


def rate_limit_wait(
    provider: str | None,
    headers: Any = None,
    body: str | None = None,
    *,
    cap: float = RETRY_AFTER_CAP_S,
) -> float | None:
    """How long to wait before re-sending the same request after a 429.

    ``None`` means *do not retry in place* — the host gave no ``Retry-After``
    hint and the provider declares no ``min_request_interval_s`` in the
    catalog, so the pre-existing breaker / recovery paths own the error.
    """
    hint = retry_after_hint(headers, body)
    if hint is not None:
        return clamp_wait(hint, cap=cap)
    interval = min_request_interval_s(provider)
    if interval > 0:
        return clamp_wait(max(interval, RETRY_AFTER_DEFAULT_S), cap=cap)
    return None


def is_rate_limited(status: int, body: str | None = None) -> bool:
    """True for a transient rate limit (429 without a quota/billing message)."""
    try:
        st = int(status or 0)
    except (TypeError, ValueError):
        return False
    if st != 429:
        return False
    try:
        from remedy.core.react_loop.errors import is_billing_llm_api_error

        if is_billing_llm_api_error(st, body or ""):
            return False
    except Exception:
        pass
    return True
