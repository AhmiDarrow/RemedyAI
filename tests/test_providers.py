"""Tests for the LLM provider adapter layer."""

from __future__ import annotations

import json

import pytest

from remedy.core.providers import (
    AnthropicProvider,
    DeepSeekProvider,
    GoogleProvider,
    OpenAIProvider,
    get_provider,
    get_provider_for_base_url,
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

    def test_google_strips_empty_tools(self):
        p = GoogleProvider()
        body = p.build_body("gemini-2.0-flash", [{"role": "user", "content": "hi"}], tools=None, stream=False)
        assert "tools" not in body

    def test_detect_provider_from_url(self):
        p = get_provider_for_base_url("https://api.anthropic.com")
        assert isinstance(p, AnthropicProvider)

        p = get_provider_for_base_url("https://api.openai.com/v1")
        assert isinstance(p, OpenAIProvider)


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
        )
        assert body["model"] == "gpt-4o-mini"
        assert body["stream"] is False
        assert body["temperature"] == 0.6  # chat / no-tools path
        assert body["max_tokens"] >= 16000  # long reviews must not hit 4k wall
        assert "tools" not in body

    def test_build_body_with_tools(self):
        p = OpenAIProvider()
        tools = [{
            "type": "function",
            "function": {"name": "search", "description": "Search", "parameters": {}},
        }]
        body = p.build_body("gpt-4o", [{"role": "user", "content": "q"}], tools=tools, stream=True)
        assert body["tools"] == tools
        assert body["tool_choice"] == "auto"
        assert body["stream"] is True
        assert body["temperature"] == 0.4  # tool path — more decisive
        assert body["max_tokens"] >= 4096

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
        assert "system" not in body
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
        assert body["system"] == "You are helpful."
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
