"""One ``remedy.llm`` INFO line per provider call — fields, no content."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from types import SimpleNamespace

import pytest

from remedy.core import agent_llm
from remedy.core.llm_binding import LlmBinding, reset_llm_binding, set_llm_binding
from remedy.core.llm_log import count_tool_calls, format_llm_line, usage_fields


def test_usage_fields_accepts_openai_and_anthropic_shapes():
    assert usage_fields({"prompt_tokens": 10, "completion_tokens": 3}) == {
        "prompt_tokens": 10,
        "completion_tokens": 3,
    }
    assert usage_fields({"input_tokens": 7, "output_tokens": 2, "cache_read_input_tokens": 5}) == {
        "prompt_tokens": 7,
        "completion_tokens": 2,
        "cache_hit_tokens": 5,
    }
    assert usage_fields(None) == {}
    assert usage_fields({"prompt_tokens": "x"}) == {}


def test_format_line_has_every_field_and_no_content():
    line = format_llm_line(
        provider="deepseek",
        model="deepseek-chat",
        session_id="sess-1234567890abcdef",
        step=3,
        latency_ms=1834.6,
        status="ok",
        finish_reason="tool_calls",
        tool_calls=2,
        usage={"prompt_tokens": 812, "completion_tokens": 96, "prompt_cache_hit_tokens": 640},
    )
    assert line == (
        "llm provider=deepseek model=deepseek-chat session=sess-1234567 step=3 "
        "status=ok latency_ms=1834 finish=tool_calls tool_calls=2 "
        "prompt_tokens=812 completion_tokens=96 cache_hit_tokens=640"
    )
    err = format_llm_line(provider="openai", model="m", status="error", error=ConnectionResetError())
    assert err.endswith("error=ConnectionResetError")
    assert "\n" not in err


def test_count_tool_calls_from_openai_shape():
    resp = {"choices": [{"message": {"tool_calls": [{"id": "a"}, {"id": "b"}]}}]}
    assert count_tool_calls(resp) == 2
    assert count_tool_calls({"choices": [{"message": {}}]}) == 0


# ---------------------------------------------------------------------------
# post_chat emits the line
# ---------------------------------------------------------------------------


class _FakeResponse:
    def __init__(self, status: int, payload: dict | None = None, text: str = "") -> None:
        self.status = status
        self._payload = payload or {}
        self._text = text

    async def json(self) -> dict:
        return self._payload

    async def text(self) -> str:
        return self._text

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _FakeSession:
    def __init__(self, *responses) -> None:
        self._queue = list(responses)

    def post(self, url, *, headers=None, json=None, timeout=None):
        item = self._queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item


@pytest.fixture
def bound():
    tok = set_llm_binding(
        LlmBinding(
            provider="openai",
            model="gpt-test",
            base_url="https://llm.example/v1",
            api_key="sk-unit",
        )
    )
    yield
    with contextlib.suppress(ValueError):
        reset_llm_binding(tok)


@pytest.fixture
def no_sleev(monkeypatch):
    import remedy.core.sleev as sleev

    def _boom(**_kw):
        raise RuntimeError("no sleev")

    monkeypatch.setattr(sleev, "prepare_llm_http", _boom)


def _runtime():
    return SimpleNamespace(config=None, _session_id="s-abc")


def _llm_records(caplog):
    return [r for r in caplog.records if r.name == "remedy.llm"]


@pytest.mark.asyncio
async def test_post_chat_logs_ok_with_usage_and_tool_calls(monkeypatch, caplog, bound, no_sleev):
    payload = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "content": None,
                    "tool_calls": [
                        {"id": "c1", "type": "function", "function": {"name": "x", "arguments": "{}"}}
                    ],
                },
            }
        ],
        "usage": {"prompt_tokens": 100, "completion_tokens": 20, "total_tokens": 120},
    }
    monkeypatch.setattr(agent_llm, "_get_shared_session", lambda: _FakeSession(_FakeResponse(200, payload)))
    caplog.set_level(logging.INFO, logger="remedy.llm")
    out = await agent_llm.post_chat(_runtime(), {"messages": [{"role": "user", "content": "SECRET"}]}, step=4)
    assert isinstance(out, dict)
    recs = _llm_records(caplog)
    assert len(recs) == 1
    line = recs[0].getMessage()
    assert recs[0].levelno == logging.INFO
    assert "provider=openai" in line and "model=gpt-test" in line
    assert "step=4" in line and "status=ok" in line
    assert "finish=tool_calls" in line and "tool_calls=1" in line
    assert "prompt_tokens=100" in line and "completion_tokens=20" in line
    assert "latency_ms=" in line
    assert "SECRET" not in line


@pytest.mark.asyncio
async def test_post_chat_logs_http_error(monkeypatch, caplog, bound, no_sleev):
    monkeypatch.setattr(
        agent_llm,
        "_get_shared_session",
        lambda: _FakeSession(_FakeResponse(500, text="boom")),
    )
    caplog.set_level(logging.INFO, logger="remedy.llm")
    out = await agent_llm.post_chat(_runtime(), {"messages": []})
    assert isinstance(out, str) and "HTTP 500" in out
    recs = _llm_records(caplog)
    assert len(recs) == 1
    assert "status=http_500" in recs[0].getMessage()
    assert recs[0].levelno == logging.WARNING


@pytest.mark.asyncio
async def test_post_chat_logs_error_type_and_reraises(monkeypatch, caplog, bound, no_sleev):
    monkeypatch.setattr(
        agent_llm,
        "_get_shared_session",
        lambda: _FakeSession(ConnectionResetError("reset")),
    )
    caplog.set_level(logging.INFO, logger="remedy.llm")
    with pytest.raises(ConnectionResetError):
        await agent_llm.post_chat(_runtime(), {"messages": []})
    recs = _llm_records(caplog)
    assert len(recs) == 1
    msg = recs[0].getMessage()
    assert "status=error" in msg and "error=ConnectionResetError" in msg


@pytest.mark.asyncio
async def test_post_chat_logs_abort_on_cancel(monkeypatch, caplog, bound, no_sleev):
    class _Hang:
        async def __aenter__(self):
            await asyncio.sleep(30)

        async def __aexit__(self, *_exc):
            return False

    class _S:
        def post(self, *a, **k):
            return _Hang()

    monkeypatch.setattr(agent_llm, "_get_shared_session", lambda: _S())
    caplog.set_level(logging.INFO, logger="remedy.llm")
    task = asyncio.create_task(agent_llm.post_chat(_runtime(), {"messages": []}))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    recs = _llm_records(caplog)
    assert len(recs) == 1
    assert "status=aborted" in recs[0].getMessage()
