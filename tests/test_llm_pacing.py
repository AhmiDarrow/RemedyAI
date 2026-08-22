"""Rate-limit backoff and request pacing for hosted LLM gateways.

The free demo gateway accepts about one request per second and answers
``429 {"code":"rate_limit_exceeded","retry_after":1}`` otherwise. A ReAct
turn fires the next model round the instant a tool result lands, so without
pacing round two always trips the limiter. Nothing here opens a socket.
"""

from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest

from remedy.core import llm_pacing
from remedy.core.llm_pacing import (
    RATE_LIMIT_MAX_RETRIES,
    RETRY_AFTER_CAP_S,
    is_rate_limited,
    min_request_interval_s,
    note_request_sent,
    pace_before_request,
    parse_retry_after,
    reset_pacing,
    seconds_until_allowed,
    sleep_abortable,
)

CATALOG = "remedy.interfaces.provider_catalog.PROVIDER_CATALOG"


@pytest.fixture(autouse=True)
def _fresh_pacing():
    reset_pacing()
    yield
    reset_pacing()


# --------------------------------------------------------------------------
# Retry-After parsing
# --------------------------------------------------------------------------


def test_retry_after_header_seconds_wins_over_body():
    assert parse_retry_after({"Retry-After": "4"}, '{"retry_after": 9}') == 4.0


def test_retry_after_header_is_case_insensitive():
    assert parse_retry_after({"retry-after": "2.5"}, "") == 2.5


def test_retry_after_from_json_body_seconds():
    body = '{"code":"rate_limit_exceeded","retry_after":1}'
    assert parse_retry_after({}, body) == 1.0


def test_retry_after_from_nested_json_body_and_ms():
    body = '{"error":{"message":"slow down","retry_after_ms":1500}}'
    assert parse_retry_after({}, body) == 1.5


def test_retry_after_from_prose_body():
    assert parse_retry_after(None, "Rate limit hit. Please try again in 3 seconds.") == 3.0


def test_retry_after_defaults_when_nothing_parses():
    assert parse_retry_after({}, "nope") == llm_pacing.RETRY_AFTER_DEFAULT_S
    assert parse_retry_after(None, None) == llm_pacing.RETRY_AFTER_DEFAULT_S


def test_retry_after_is_capped_and_floored():
    assert parse_retry_after({"Retry-After": "600"}, "") == RETRY_AFTER_CAP_S
    assert parse_retry_after({}, '{"retry_after": 0}') == 0.25


def test_retry_after_http_date_in_the_past_is_zero_floor():
    assert parse_retry_after({"Retry-After": "Wed, 21 Oct 2015 07:28:00 GMT"}, "") == 0.25


def test_is_rate_limited_excludes_quota_billing():
    assert is_rate_limited(429, '{"code":"rate_limit_exceeded"}')
    assert not is_rate_limited(429, '{"error":{"code":"insufficient_quota"}}')
    assert not is_rate_limited(500, "rate limit")
    assert RATE_LIMIT_MAX_RETRIES >= 1


# --------------------------------------------------------------------------
# Pacing from catalog meta
# --------------------------------------------------------------------------


def test_min_interval_defaults_to_zero_without_catalog_key():
    with patch(CATALOG, {"openai": {"label": "OpenAI"}}):
        assert min_request_interval_s("openai") == 0.0
        assert min_request_interval_s("unknown") == 0.0
        assert min_request_interval_s(None) == 0.0


def test_min_interval_read_from_catalog_meta():
    with patch(CATALOG, {"demo": {"min_request_interval_s": 1.1}}):
        assert min_request_interval_s("demo") == 1.1
        assert min_request_interval_s("DEMO") == 1.1


def test_seconds_until_allowed_tracks_last_request():
    with patch(CATALOG, {"demo": {"min_request_interval_s": 1.0}}):
        assert seconds_until_allowed("demo", now=100.0) == 0.0
        note_request_sent("demo", now=100.0)
        assert seconds_until_allowed("demo", now=100.2) == pytest.approx(0.8)
        assert seconds_until_allowed("demo", now=101.5) == 0.0
        # Unpaced providers never wait, even right after a call.
        note_request_sent("openai", now=100.0)
        assert seconds_until_allowed("openai", now=100.0) == 0.0


@pytest.mark.asyncio
async def test_pace_before_request_sleeps_only_when_too_soon():
    slept: list[float] = []

    async def fake_sleep(seconds, abort_ev=None):
        slept.append(seconds)

    with (
        patch(CATALOG, {"demo": {"min_request_interval_s": 1.0}}),
        patch.object(llm_pacing, "sleep_abortable", fake_sleep),
    ):
        first = await pace_before_request("demo")
        second = await pace_before_request("demo")
    assert first == 0.0
    assert 0.0 < second <= 1.0
    assert slept == [second]


@pytest.mark.asyncio
async def test_sleep_abortable_raises_when_stop_pressed():
    ev = asyncio.Event()

    async def press_stop():
        await asyncio.sleep(0.01)
        ev.set()

    asyncio.get_running_loop().create_task(press_stop())
    with pytest.raises(asyncio.CancelledError):
        await sleep_abortable(5.0, ev)


@pytest.mark.asyncio
async def test_sleep_abortable_returns_after_timeout():
    ev = asyncio.Event()
    await sleep_abortable(0.01, ev)
    await sleep_abortable(0.0, None)


# --------------------------------------------------------------------------
# Demo is a hosted gateway, not a local host
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model", ["codestral-latest", "gemini-3.1-flash-lite", "gpt-oss:20b"]
)
def test_demo_provider_is_not_local(model):
    from remedy.core.local_agent_optimize import is_local_binding
    from remedy.nanoswarm.token_nanobot import is_local_model

    assert not is_local_model("demo", model, base_url="https://api.llm7.io/v1")
    assert not is_local_binding("demo", model, "https://api.llm7.io/v1")


def test_ollama_and_rmb_still_local():
    from remedy.nanoswarm.token_nanobot import is_local_model

    assert is_local_model("ollama", "qwen2.5:7b")
    assert is_local_model("custom", "x", base_url="http://127.0.0.1:8787/v1")


def test_demo_context_window_is_hosted_sized_but_below_gateway_cap(monkeypatch):
    from remedy.nanoswarm.token_nanobot import (
        _LOCAL_DEFAULT_WINDOW,
        clear_context_window_cache,
        resolve_context_window,
    )

    monkeypatch.delenv("REMEDY_CONTEXT_WINDOW", raising=False)
    clear_context_window_cache()
    for model in ("codestral-latest", "gemini-3.1-flash-lite", "gpt-oss:20b"):
        win = resolve_context_window("demo", model, base_url="https://api.llm7.io/v1")
        assert win > _LOCAL_DEFAULT_WINDOW
        assert win <= 32_768


# --------------------------------------------------------------------------
# retry-in-place only with a real hint or a declared interval
# --------------------------------------------------------------------------


def test_retry_after_hint_is_none_without_any_hint():
    from remedy.core.llm_pacing import retry_after_hint

    assert retry_after_hint({}, "rate limited") is None
    assert retry_after_hint(None, None) is None
    assert retry_after_hint({"Retry-After": "2"}, "") == 2.0
    assert retry_after_hint({}, '{"retry_after": 1}') == 1.0


def test_rate_limit_wait_is_none_for_bare_429_on_unpaced_provider():
    from remedy.core.llm_pacing import rate_limit_wait

    with patch(CATALOG, {"openai": {"label": "OpenAI"}}):
        assert rate_limit_wait("openai", {}, "rate limited") is None


def test_rate_limit_wait_uses_hint_or_catalog_interval():
    from remedy.core.llm_pacing import rate_limit_wait

    with patch(CATALOG, {"demo": {"min_request_interval_s": 1.1}}):
        assert rate_limit_wait("demo", {}, "busy") == 2.0  # interval floor → default
        assert rate_limit_wait("demo", {"Retry-After": "5"}, "") == 5.0
        assert rate_limit_wait("demo", {"Retry-After": "900"}, "") == RETRY_AFTER_CAP_S
    with patch(CATALOG, {"openai": {}}):
        assert rate_limit_wait("openai", {}, '{"retry_after_ms": 500}') == 0.5
