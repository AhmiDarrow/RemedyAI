"""Local fixes: provider circuit breaker, stop-intent override, mission auto-fail."""

from __future__ import annotations

# ---------------------------------------------------------------------------
# Provider circuit breaker (providers.py)
# ---------------------------------------------------------------------------


def test_breaker_quarantines_after_three_api_errors() -> None:
    from remedy.core import providers

    providers.clear_provider_quarantine()
    try:
        assert providers.record_provider_error("xai", 403) is False
        assert providers.record_provider_error("xai", 403) is False
        assert providers.provider_quarantined("xai") is False
        # Third consecutive API/billing error trips the quarantine.
        assert providers.record_provider_error("xai", 403) is True
        assert providers.provider_quarantined("xai") is True
    finally:
        providers.clear_provider_quarantine()


def test_breaker_success_resets_streak() -> None:
    from remedy.core import providers

    providers.clear_provider_quarantine()
    try:
        providers.record_provider_error("openai", 429)
        providers.record_provider_error("openai", 429)
        providers.record_provider_success("openai")
        assert providers.record_provider_error("openai", 429) is False
        assert providers.provider_quarantined("openai") is False
    finally:
        providers.clear_provider_quarantine()


def test_breaker_ignores_non_api_statuses_and_local() -> None:
    from remedy.core import providers

    providers.clear_provider_quarantine()
    try:
        # 200/400 (non-billing, non-API-auth) don't accumulate.
        assert providers.record_provider_error("openai", 400) is False
        assert providers.record_provider_error("openai", 200) is False
        assert providers.provider_quarantined("openai") is False
        # Local providers are never quarantined.
        for _ in range(5):
            assert providers.record_provider_error("ollama", 503) is False
        assert providers.provider_quarantined("ollama") is False
    finally:
        providers.clear_provider_quarantine()


# ---------------------------------------------------------------------------
# Stop intent (react_policy.py) — mission continuity cannot override it
# ---------------------------------------------------------------------------


def test_message_asks_to_stop_detects_stop_requests() -> None:
    from remedy.core.react_policy import message_asks_to_stop

    assert message_asks_to_stop("stop") is True
    assert message_asks_to_stop("stop the mission") is True
    assert message_asks_to_stop("Cancel everything") is True
    assert message_asks_to_stop("please halt now") is True
    assert message_asks_to_stop("never mind, forget it") is True
    assert message_asks_to_stop("do not continue") is True
    # Normal prose is not a stop request.
    assert message_asks_to_stop("stop at the store and buy milk") is False
    assert message_asks_to_stop("review this code") is False
    assert message_asks_to_stop("") is False


def test_right_size_max_tokens_never_truncates() -> None:
    """agent._right_size_max_tokens returns provider cap, never 0."""
    from remedy.core.agent import BasicRuntime
    from remedy.core.providers import MAX_OUTPUT_TOKENS

    rt = object.__new__(BasicRuntime)
    rt._llm_max_output_tokens = None  # type: ignore[attr-defined]
    rt._provider = None  # type: ignore[attr-defined]
    rt._llm_model = "deepseek-v4-flash"
    cap = rt._right_size_max_tokens()  # type: ignore[attr-defined]
    assert cap >= 1
    assert int(rt._llm_max_output_tokens) >= 1  # type: ignore[attr-defined]
    # No binding → default cloud budget, never smaller than a sane floor.
    assert cap == MAX_OUTPUT_TOKENS


# ---------------------------------------------------------------------------
# compress_context one-shot (agent_memory_tools.py)
# ---------------------------------------------------------------------------


def test_compress_context_one_shot_and_no_rearm_note() -> None:
    from remedy.core.agent_memory_tools import register_memory_tools

    class FakeBrief:
        intent = ""
        decisions: list[str] = []
        blockers: list[str] = []
        history_thread: list[object] = []
        artifacts: list[str] = []
        compress_count = 0
        last_quality_score = None

        def append_history_thread(self, *a, **k) -> None:
            self.history_thread.append(a[0])

        def touch(self) -> None:
            pass

    class FakeRuntime:
        def __init__(self) -> None:
            self.memory = None
            self._session_id = "sess-test"
            self._session_brief = FakeBrief()
            self._compress_done_sessions = set()
            self.tool_registry = _FakeRegistry()

    class _FakeRegistry:
        handlers: dict[str, object] = {}

        def register_builtin_handler(self, name, desc, fn, schema) -> None:
            self.handlers[name] = fn

    rt = FakeRuntime()
    register_memory_tools(rt)
    handler = rt.tool_registry.handlers["compress_context"]

    async def run() -> str:
        # Session history loader is None-memory safe via suppress.
        return await handler(focus="local fixes")

    import asyncio

    first = asyncio.run(run())
    assert "Do not re-arm cancelled missions" in first
    # One-shot: second call in same session is a no-op.
    second = asyncio.run(run())
    assert "already compressed" in second

    # The no-re-arm instruction lands in the brief thread.
    thread_text = "\n".join(str(x) for x in rt._session_brief.history_thread)
    assert "Do not re-arm cancelled missions" in thread_text
