"""LLM provider adapter layer.

Translates between Remedy's internal OpenAI-compatible message/tool format
and each provider's native API contract. The agent loop operates on a
single canonical format; providers handle the per-API translation.
"""

from __future__ import annotations

import json
import logging
from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from typing import Any

logger = logging.getLogger(__name__)


class ProviderAdapter(ABC):
    """Abstract base for an LLM provider API adapter."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier (e.g. 'openai', 'anthropic')."""

    @property
    @abstractmethod
    def default_base_url(self) -> str:
        """Fallback base URL when none is configured."""

    @abstractmethod
    def auth_headers(self, api_key: str) -> dict[str, str]:
        """Return the HTTP headers required for authentication."""

    @abstractmethod
    def chat_endpoint(self, base_url: str) -> str:
        """Return the full chat completions endpoint URL."""

    @abstractmethod
    def build_body(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        """Build the JSON request body for a chat completion call.

        Receives messages in OpenAI format with roles: system, user, assistant, tool.
        Returns provider-native body dict.
        """

    @abstractmethod
    def extract_response(self, response_json: dict[str, Any]) -> dict[str, Any]:
        """Extract the canonical response dict from the provider's raw JSON.

        Returns a dict with shape compatible with OpenAI's choice message:
        {"content": str, "tool_calls": [{"id": str, "type": "function",
         "function": {"name": str, "arguments": str}}] | None}
        """

    @abstractmethod
    def extract_finish_reason(self, response_json: dict[str, Any]) -> str | None:
        """Return the finish reason string (e.g. 'stop', 'tool_calls', 'length')."""

    async def parse_stream(
        self,
        response: Any,  # aiohttp.ClientResponse
    ) -> AsyncIterator[str]:
        """Yield content deltas from a streaming response.

        Default implementation yields no tokens (subclasses may override).
        """
        yield  # pragma: no cover


# ---------------------------------------------------------------------------
# OpenAI-compatible provider
# (works for OpenAI, DeepSeek, OpenRouter, Ollama, Google via /v1beta/openai)
# ---------------------------------------------------------------------------


class OpenAIProvider(ProviderAdapter):
    """Adapter for OpenAI and OpenAI-compatible APIs."""

    provider_name = "openai"
    default_base_url = "https://api.openai.com/v1"

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }

    def chat_endpoint(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/chat/completions"

    def build_body(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": 0.7,
            "max_tokens": 4096,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        return body

    def extract_response(self, response_json: dict[str, Any]) -> dict[str, Any]:
        choice = (response_json.get("choices") or [{}])[0]
        msg = choice.get("message") or choice.get("delta") or {}
        return {
            "content": (msg.get("content") or "").strip() or None,
            "tool_calls": msg.get("tool_calls"),
        }

    def extract_finish_reason(self, response_json: dict[str, Any]) -> str | None:
        choice = (response_json.get("choices") or [{}])[0]
        return choice.get("finish_reason")

    async def parse_stream(
        self,
        response: Any,
    ) -> AsyncIterator[str]:
        async for line in response.content:
            text = line.decode("utf-8").strip()
            if not text or text.startswith(":"):
                continue
            if text == "data: [DONE]":
                break
            if text.startswith("data: "):
                text = text[6:]
            try:
                chunk = json.loads(text)
            except json.JSONDecodeError:
                continue
            choice = (chunk.get("choices") or [{}])[0]
            delta = choice.get("delta") or {}
            content = delta.get("content")
            if content:
                yield content


# ---------------------------------------------------------------------------
# Anthropic provider
# ---------------------------------------------------------------------------


class AnthropicProvider(ProviderAdapter):
    """Adapter for Anthropic's Messages API.

    Translates between Remedy's OpenAI-format internal representation and
    Anthropic's native API contract (system as top-level field, content blocks,
    tool_use / tool_result blocks).
    """

    provider_name = "anthropic"
    default_base_url = "https://api.anthropic.com"

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def chat_endpoint(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/v1/messages"

    def build_body(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
    ) -> dict[str, Any]:
        system_prompt, converted = self._convert_messages(messages)
        body: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": 4096,
            "stream": stream,
        }
        if system_prompt:
            body["system"] = system_prompt
        if tools:
            body["tools"] = self._convert_tools(tools)
        return body

    def extract_response(self, response_json: dict[str, Any]) -> dict[str, Any]:
        content_list = response_json.get("content") or []
        return self._parse_anthropic_content(content_list)

    def extract_finish_reason(self, response_json: dict[str, Any]) -> str | None:
        reason = response_json.get("stop_reason")
        if reason == "end_turn":
            return "stop"
        if reason == "tool_use":
            return "tool_calls"
        if reason == "max_tokens":
            return "length"
        return reason

    # -- private translation helpers -----------------------------------------

    @staticmethod
    def _convert_messages(
        openai_msgs: list[dict[str, Any]],
    ) -> tuple[str, list[dict[str, Any]]]:
        """Convert OpenAI messages to Anthropic messages + system string.

        Returns (system_prompt, anthropic_messages).
        """
        system_texts: list[str] = []
        anthropic: list[dict[str, Any]] = []

        for msg in openai_msgs:
            role = msg.get("role", "user")
            if role == "system":
                content = msg.get("content") or ""
                if isinstance(content, str) and content.strip():
                    system_texts.append(content.strip())
                continue

            if role == "user":
                anthropic.append({"role": "user", "content": msg.get("content") or ""})

            elif role == "assistant":
                content_blocks: list[dict[str, Any]] = []
                text = msg.get("content")
                if text:
                    content_blocks.append({"type": "text", "text": text})

                for tc in msg.get("tool_calls") or []:
                    fn = tc.get("function") or {}
                    content_blocks.append({
                        "type": "tool_use",
                        "id": tc.get("id") or "",
                        "name": fn.get("name") or "",
                        "input": AnthropicProvider._safe_json(fn.get("arguments")),
                    })

                if not content_blocks:
                    content_blocks.append({"type": "text", "text": ""})
                anthropic.append({"role": "assistant", "content": content_blocks})

            elif role == "tool":
                anthropic.append({
                    "role": "user",
                    "content": [{
                        "type": "tool_result",
                        "tool_use_id": msg.get("tool_call_id") or "",
                        "content": str(msg.get("content") or ""),
                    }],
                })

        return "\n\n".join(system_texts), anthropic

    @staticmethod
    def _convert_tools(
        openai_tools: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Convert OpenAI function tools to Anthropic tool format."""
        converted: list[dict[str, Any]] = []
        for t in openai_tools:
            fn = t.get("function") or {}
            converted.append({
                "name": fn.get("name") or "",
                "description": fn.get("description") or "",
                "input_schema": fn.get("parameters") or {
                    "type": "object", "properties": {}
                },
            })
        return converted

    @staticmethod
    def _parse_anthropic_content(
        content_list: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Parse Anthropic content blocks into OpenAI-compatible response dict."""
        text_parts: list[str] = []
        tool_calls: list[dict[str, Any]] = []

        for block in content_list:
            if block.get("type") == "text":
                text_parts.append(block.get("text") or "")
            elif block.get("type") == "tool_use":
                tool_calls.append({
                    "id": block.get("id") or "",
                    "type": "function",
                    "function": {
                        "name": block.get("name") or "",
                        "arguments": json.dumps(
                            block.get("input") or {},
                            default=str,
                        ),
                    },
                })

        return {
            "content": ("\n".join(text_parts).strip() or None),
            "tool_calls": tool_calls or None,
        }

    @staticmethod
    def _safe_json(value: Any) -> dict[str, Any]:
        """Parse a value to JSON dict, returning {} on failure."""
        if isinstance(value, dict):
            return value
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (json.JSONDecodeError, TypeError):
                return {}
        return {}

    async def parse_stream(
        self,
        response: Any,
    ) -> AsyncIterator[str]:
        async for line in response.content:
            text = line.decode("utf-8").strip()
            if not text or text.startswith(":"):
                continue
            if text.startswith("data: "):
                text = text[6:]
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                continue
            if event.get("type") == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta":
                    yield delta.get("text") or ""


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


_PROVIDERS: dict[str, type[ProviderAdapter]] = {
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": OpenAIProvider,         # Google via /v1beta/openai is OpenAI-compatible
    "deepseek": OpenAIProvider,       # DeepSeek is OpenAI-compatible
    "openrouter": OpenAIProvider,     # OpenRouter is OpenAI-compatible
    "ollama": OpenAIProvider,         # Ollama is OpenAI-compatible
    "custom": OpenAIProvider,         # Unknown custom endpoints default to OpenAI-compatible
}


def get_provider(provider_name: str) -> ProviderAdapter:
    """Return a provider adapter instance for the named provider.

    Falls back to OpenAI-compatible for unknown providers.
    """
    cls = _PROVIDERS.get(provider_name.lower(), OpenAIProvider)
    return cls()


def get_provider_for_base_url(base_url: str) -> ProviderAdapter:
    """Heuristically detect the provider from the base URL."""
    url_lower = base_url.lower()
    if "anthropic" in url_lower:
        return get_provider("anthropic")
    return get_provider("openai")
