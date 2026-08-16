"""Tests for the LLM provider adapter layer."""

from __future__ import annotations

import json

from remedy.core.providers import (
    AnthropicProvider,
    DeepSeekProvider,
    GoogleProvider,
    LlamaCppProvider,
    OpenAIProvider,
    RmbProvider,
    get_provider,
    get_provider_for_base_url,
    select_provider,
)


class TestProviderRegistry:
    """Provider lookup and detection."""

    def test_get_provider_by_name(self):
        p = get_provider("openai")
        assert isinstance(p, OpenAIProvider)

        p = get_provider("anthropic")
        assert isinstance(p, AnthropicProvider)

        p = get_provider("google")
        assert isinstance(p, GoogleProvider)
        assert isinstance(p, OpenAIProvider)

        p = get_provider("deepseek")
        assert isinstance(p, DeepSeekProvider)

        p = get_provider("unknown")
        assert isinstance(p, OpenAIProvider)

    def test_openai_compatible_providers_use_sse_stream(self):
        """DeepSeek etc. return text/event-stream — must not be read via resp.json()."""
        for name in ("openai", "deepseek", "google", "openrouter", "ollama", "custom"):
            p = get_provider(name)
            assert p.uses_openai_sse is True, name
        assert get_provider("anthropic").uses_openai_sse is False

    def test_google_strips_empty_tools(self):
        p = GoogleProvider()
        body = p.build_body("gemini-2.0-flash", [{"role": "user", "content": "hi"}], tools=None, stream=False)
        assert "tools" not in body

    def test_detect_provider_from_url(self):
        p = get_provider_for_base_url("https://api.anthropic.com")
        assert isinstance(p, AnthropicProvider)

        p = get_provider_for_base_url("https://api.openai.com/v1")
        assert isinstance(p, OpenAIProvider)

        # Local path: Ollama / 11434 URLs resolve to the llama.cpp adapter.
        p = get_provider_for_base_url("http://127.0.0.1:11434/v1")
        assert isinstance(p, LlamaCppProvider)
        assert isinstance(p, OpenAIProvider)  # still OpenAI-compatible

    def test_ollama_and_llamacpp_both_use_local_adapter(self):
        assert isinstance(get_provider("ollama"), LlamaCppProvider)
        assert isinstance(get_provider("llamacpp"), LlamaCppProvider)

    def test_loopback_base_url_resolves_to_local_adapter(self):
        """localhost / KoboldCpp endpoints must drop cloud max_tokens."""
        for url in (
            "http://localhost:5001/v1/",
            "http://127.0.0.1:5001/v1",
            "http://localhost:5001/api/v1/chat/completions",
        ):
            p = get_provider_for_base_url(url)
            assert isinstance(p, LlamaCppProvider), url

    def test_select_provider_custom_with_loopback_url(self):
        """Config provider='custom' + localhost base → LlamaCppProvider."""
        p = select_provider("custom", "http://localhost:5001/v1/")
        assert isinstance(p, LlamaCppProvider)

    def test_select_provider_custom_with_cloud_url(self):
        """custom + non-loopback URL keeps the OpenAI-compatible adapter."""
        p = select_provider("custom", "https://llm-proxy.example.com/v1")
        assert isinstance(p, OpenAIProvider)
        assert not isinstance(p, LlamaCppProvider)

    def test_select_provider_known_name_ignores_leftover_loopback(self):
        """A named cloud provider must not become llamacpp because RMB left a loopback URL."""
        p = select_provider("deepseek", "http://localhost:5001/v1/")
        assert isinstance(p, DeepSeekProvider)
        p2 = select_provider("deepseek", "https://api.deepseek.com")
        assert isinstance(p2, DeepSeekProvider)

    def test_select_provider_custom_without_url(self):
        assert isinstance(select_provider("custom", ""), OpenAIProvider)


class TestOpenAIProvider:
    """OpenAI-compatible adapter."""

    def test_default_base_url(self):
        p = OpenAIProvider()
        assert p.default_base_url == "https://api.openai.com/v1"

    def test_auth_headers(self):
        p = OpenAIProvider()
        h = p.auth_headers("sk-test")
        assert h["Authorization"] == "Bearer sk-test"
        assert h["Content-Type"] == "application/json"

    def test_chat_endpoint(self):
        p = OpenAIProvider()
        assert p.chat_endpoint("https://api.openai.com/v1") == "https://api.openai.com/v1/chat/completions"
        assert p.chat_endpoint("https://api.openai.com/v1/") == "https://api.openai.com/v1/chat/completions"

    def test_build_body_minimal(self):
        p = OpenAIProvider()
        body = p.build_body(
            "gpt-4o-mini",
            [{"role": "user", "content": "hello"}],
            tools=None,
            stream=False,
            thinking_level="medium",
        )
        assert body["model"] == "gpt-4o-mini"
        assert body["stream"] is False
        assert body["temperature"] == 0.6  # chat / no-tools path
        assert body["max_tokens"] >= 128_000  # never throttle completion length
        assert "tools" not in body

    def test_build_body_with_tools(self):
        p = OpenAIProvider()
        tools = [{
            "type": "function",
            "function": {"name": "search", "description": "Search", "parameters": {}},
        }]
        body = p.build_body(
            "gpt-4o",
            [{"role": "user", "content": "q"}],
            tools=tools,
            stream=True,
            thinking_level="medium",
        )
        assert body["tools"] == tools
        assert body["tool_choice"] == "auto"
        assert body["stream"] is True
        assert body["temperature"] == 0.4  # tool path — more decisive
        assert body["max_tokens"] >= 128_000  # full budget with tools too

    def test_build_body_never_throttles_by_thinking_level(self):
        p = OpenAIProvider()
        for level in ("off", "low", "medium", "high"):
            body = p.build_body(
                "gpt-4o",
                [{"role": "user", "content": "q"}],
                tools=None,
                stream=False,
                thinking_level=level,
            )
            assert body["max_tokens"] >= 128_000, level

    def test_extract_response_text(self):
        p = OpenAIProvider()
        data = {
            "choices": [{
                "message": {"role": "assistant", "content": "Hello world"},
                "finish_reason": "stop",
            }],
        }
        result = p.extract_response(data)
        assert result["content"] == "Hello world"
        assert result["tool_calls"] is None

    def test_extract_response_empty(self):
        p = OpenAIProvider()
        data = {"choices": [{"message": {}}]}
        result = p.extract_response(data)
        assert result["content"] is None
        assert result["tool_calls"] is None

    def test_extract_response_tool_calls(self):
        p = OpenAIProvider()
        data = {
            "choices": [{
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"function": {"name": "test", "arguments": "{}"}}],
                },
            }],
        }
        result = p.extract_response(data)
        assert result["content"] is None
        assert result["tool_calls"] is not None
        assert len(result["tool_calls"]) == 1

    def test_extract_finish_reason(self):
        p = OpenAIProvider()
        data = {"choices": [{"finish_reason": "stop"}]}
        assert p.extract_finish_reason(data) == "stop"

        data = {"choices": [{}]}
        assert p.extract_finish_reason(data) is None


class TestLlamaCppProvider:
    """Local llama.cpp / RMB adapter — moderate max_tokens, no cloud-only fields."""

    def _body(self, tools=None, model="llama3.2"):
        p = LlamaCppProvider()
        return p.build_body(
            model,
            [{"role": "user", "content": "hello"}],
            tools=tools,
            stream=True,
            thinking_level="high",
        )

    def test_max_tokens_moderate_not_cloud_scale(self):
        body = self._body()
        # Must send a real budget but never cloud-scale (128k).
        assert "max_tokens" in body
        assert 256 <= int(body["max_tokens"]) <= 2048

    def test_max_tokens_moderate_with_tools(self):
        tools = [{
            "type": "function",
            "function": {"name": "search", "description": "Search", "parameters": {}},
        }]
        body = self._body(tools=tools)
        assert 256 <= int(body["max_tokens"]) <= 2048
        assert float(body.get("temperature", 1)) <= 0.2

    def test_reasoning_effort_never_sent(self):
        # Even a local model whose name suggests a reasoner must not trigger it.
        body = self._body(model="qwen-reasoner")
        assert "reasoning_effort" not in body

    def test_still_openai_compatible_stream(self):
        p = LlamaCppProvider()
        assert p.uses_openai_sse is True
        assert p.provider_name == "llamacpp"

    def test_rmb_url_selects_rmb_provider(self):
        p = get_provider_for_base_url("http://127.0.0.1:8787/v1")
        assert isinstance(p, RmbProvider)
        p2 = select_provider("custom", "http://127.0.0.1:8787/v1")
        assert isinstance(p2, RmbProvider)
        body = p2.build_body(
            "Qwen2.5-Coder-7B-Instruct-Q4_K_M",
            [{"role": "user", "content": "hi"}],
            tools=None,
            stream=False,
        )
        assert 256 <= int(body["max_tokens"]) <= 3072
        # No legacy RMB4 continuous payload
        assert "rmb" not in body or body.get("rmb") is None


class TestAnthropicProvider:
    """Anthropic Messages API adapter."""

    def test_default_base_url(self):
        p = AnthropicProvider()
        assert p.default_base_url == "https://api.anthropic.com"

    def test_auth_headers(self):
        p = AnthropicProvider()
        h = p.auth_headers("sk-ant-test")
        assert h["x-api-key"] == "sk-ant-test"
        assert h["anthropic-version"] == "2023-06-01"

    def test_chat_endpoint(self):
        p = AnthropicProvider()
        assert p.chat_endpoint("https://api.anthropic.com") == "https://api.anthropic.com/v1/messages"

    def test_chat_endpoint_does_not_double_v1(self):
        """Settings catalog stores ``…/v1``; doubling it 404s as not_found."""
        p = AnthropicProvider()
        assert (
            p.chat_endpoint("https://api.anthropic.com/v1")
            == "https://api.anthropic.com/v1/messages"
        )
        assert (
            p.chat_endpoint("https://api.anthropic.com/v1/")
            == "https://api.anthropic.com/v1/messages"
        )
        assert (
            p.chat_endpoint("https://api.anthropic.com/v1/messages")
            == "https://api.anthropic.com/v1/messages"
        )

    # -- message conversion ---------------------------------------------------

    def test_convert_messages_simple(self):
        p = AnthropicProvider()
        system, msgs = p._convert_messages([
            {"role": "system", "content": "You are helpful."},
            {"role": "user", "content": "Hello"},
        ])
        assert system == "You are helpful."
        assert msgs == [{"role": "user", "content": "Hello"}]

    def test_convert_messages_multiple_system(self):
        p = AnthropicProvider()
        system, msgs = p._convert_messages([
            {"role": "system", "content": "Rule 1"},
            {"role": "system", "content": "Rule 2"},
            {"role": "user", "content": "ok"},
        ])
        assert system == "Rule 1\n\nRule 2"
        assert len(msgs) == 1

    def test_convert_messages_assistant_tool_calls(self):
        p = AnthropicProvider()
        _, msgs = p._convert_messages([
            {"role": "user", "content": "search for cats"},
            {
                "role": "assistant",
                "content": "Let me search.",
                "tool_calls": [{
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": "search", "arguments": json.dumps({"query": "cats"})},
                }],
            },
        ])
        assert len(msgs) == 2
        assert msgs[0]["role"] == "user"
        assert msgs[1]["role"] == "assistant"

        content = msgs[1]["content"]
        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "Let me search."
        assert content[1]["type"] == "tool_use"
        assert content[1]["name"] == "search"
        assert content[1]["input"] == {"query": "cats"}

    def test_convert_messages_tool_result(self):
        p = AnthropicProvider()
        _, msgs = p._convert_messages([
            {"role": "user", "content": "hello"},
            {
                "role": "tool",
                "tool_call_id": "call_1",
                "content": "result: found 3 cats",
            },
        ])
        assert len(msgs) == 2
        assert msgs[1]["role"] == "user"
        assert isinstance(msgs[1]["content"], list)
        assert msgs[1]["content"][0]["type"] == "tool_result"
        assert msgs[1]["content"][0]["tool_use_id"] == "call_1"

    def test_convert_messages_merges_adjacent_tool_results(self):
        p = AnthropicProvider()
        _, msgs = p._convert_messages([
            {"role": "user", "content": "go"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c1", "function": {"name": "a", "arguments": "{}"}},
                    {"id": "c2", "function": {"name": "b", "arguments": "{}"}},
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "one"},
            {"role": "tool", "tool_call_id": "c2", "content": "two"},
        ])
        assert msgs[-1]["role"] == "user"
        kinds = [b["type"] for b in msgs[-1]["content"]]
        assert kinds == ["tool_result", "tool_result"]
        assert [b["tool_use_id"] for b in msgs[-1]["content"]] == ["c1", "c2"]

    def test_convert_messages_empty_system_skipped(self):
        p = AnthropicProvider()
        system, msgs = p._convert_messages([
            {"role": "system", "content": ""},
            {"role": "user", "content": "hi"},
        ])
        assert system == ""
        assert len(msgs) == 1

    # -- tools conversion -----------------------------------------------------

    def test_convert_tools(self):
        p = AnthropicProvider()
        openai_tools = [{
            "type": "function",
            "function": {
                "name": "get_weather",
                "description": "Get the weather",
                "parameters": {"type": "object", "properties": {"location": {"type": "string"}}},
            },
        }]
        result = p._convert_tools(openai_tools)
        assert len(result) == 1
        assert result[0]["name"] == "get_weather"
        assert result[0]["input_schema"]["properties"]["location"]["type"] == "string"

    # -- response parsing -----------------------------------------------------

    def test_extract_response_text(self):
        p = AnthropicProvider()
        data = {
            "content": [{"type": "text", "text": "Hello from Claude"}],
            "stop_reason": "end_turn",
        }
        result = p.extract_response(data)
        assert result["content"] == "Hello from Claude"
        assert result["tool_calls"] is None

    def test_extract_response_tool_use(self):
        p = AnthropicProvider()
        data = {
            "content": [{
                "type": "tool_use",
                "id": "toolu_001",
                "name": "search",
                "input": {"query": "cats"},
            }],
            "stop_reason": "tool_use",
        }
        result = p.extract_response(data)
        assert result["content"] is None
        assert len(result["tool_calls"]) == 1
        assert result["tool_calls"][0]["id"] == "toolu_001"
        assert result["tool_calls"][0]["type"] == "function"
        assert result["tool_calls"][0]["function"]["name"] == "search"

    def test_extract_response_mixed(self):
        p = AnthropicProvider()
        data = {
            "content": [
                {"type": "text", "text": "Let me search for that."},
                {
                    "type": "tool_use",
                    "id": "toolu_002",
                    "name": "search",
                    "input": {"query": "dogs"},
                },
            ],
        }
        result = p.extract_response(data)
        assert result["content"] == "Let me search for that."
        assert len(result["tool_calls"]) == 1

    def test_extract_finish_reason(self):
        p = AnthropicProvider()
        assert p.extract_finish_reason({"stop_reason": "end_turn"}) == "stop"
        assert p.extract_finish_reason({"stop_reason": "tool_use"}) == "tool_calls"
        assert p.extract_finish_reason({"stop_reason": "max_tokens"}) == "length"

    # -- build_body -----------------------------------------------------------

    def test_build_body_minimal(self):
        p = AnthropicProvider()
        body = p.build_body(
            "claude-sonnet-4-20250514",
            [{"role": "user", "content": "hello"}],
            tools=None,
            stream=False,
        )
        assert body["model"] == "claude-sonnet-4-20250514"
        assert body["stream"] is False
        # Default thinking_level injects a short system nudge when none provided.
        assert "system" in body
        assert "Thinking" in str(body["system"])
        assert "tools" not in body

    def test_build_body_with_system(self):
        p = AnthropicProvider()
        body = p.build_body(
            "claude-3-haiku",
            [
                {"role": "system", "content": "You are helpful."},
                {"role": "user", "content": "hi"},
            ],
            tools=None,
            stream=True,
        )
        # User system retained; may be combined with thinking nudge.
        assert "You are helpful." in str(body.get("system") or "")
        assert body["stream"] is True
        assert len(body["messages"]) == 1
        assert body["messages"][0]["role"] == "user"

    def test_build_body_with_tools(self):
        p = AnthropicProvider()
        tools = [{"type": "function", "function": {"name": "test", "description": "t", "parameters": {}}}]
        body = p.build_body(
            "claude-sonnet-4-20250514",
            [{"role": "user", "content": "q"}],
            tools=tools,
            stream=False,
        )
        assert "tools" in body
        assert body["tools"][0]["name"] == "test"
