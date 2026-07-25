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

_THINKING_NUDGES = {
    "off": (
        "Thinking level: off. Prefer action over long internal monologue. "
        "Still give complete answers — never cut off mid-thought or mid-reply."
    ),
    "low": (
        "Thinking level: low. Light reasoning when useful. "
        "Still give complete answers and full tool work — never truncate."
    ),
    "medium": (
        "Thinking level: medium. Think step-by-step when useful, then answer fully. "
        "Never cut off mid-section; finish the full response."
    ),
    "high": (
        "Thinking level: high. Reason carefully before tools or final answers. "
        "Check edge cases and verify tool results. "
        "Never truncate thinking or answers — complete every response fully."
    ),
}

# Default completion budget (OpenAI-compat / xAI / etc.).
# We never lower this for thinking_level. Auto-continue covers true hard walls.
# Per-provider subclasses may raise provider_max_output_tokens() when the API
# rejects absurd values (e.g. DeepSeek historically capped ~8k–64k).
MAX_OUTPUT_TOKENS = 128_000


def _thinking_nudge(level: str) -> str:
    return _THINKING_NUDGES.get((level or "medium").lower(), "")


def _prepend_system_nudge(
    messages: list[dict[str, Any]], nudge: str
) -> list[dict[str, Any]]:
    if not nudge:
        return messages
    out = list(messages)
    if out and out[0].get("role") == "system":
        base = str(out[0].get("content") or "")
        if nudge not in base:
            out[0] = {**out[0], "content": f"{base}\n\n{nudge}".strip()}
        return out
    return [{"role": "system", "content": nudge}, *out]


def _model_wants_reasoning_effort(model: str) -> bool:
    m = (model or "").lower()
    return any(
        x in m
        for x in (
            "o1",
            "o3",
            "o4",
            "gpt-5",
            "grok-3-mini",
            "reasoning",
            "think",
        )
    )


class ProviderAdapter(ABC):
    """Abstract base for an LLM provider API adapter."""

    @property
    @abstractmethod
    def provider_name(self) -> str:
        """Human-readable provider identifier (e.g. 'openai', 'anthropic')."""

    @property
    def uses_openai_sse(self) -> bool:
        """True when ``stream=True`` returns OpenAI-style ``text/event-stream`` SSE.

        DeepSeek, OpenRouter, Ollama, Google OpenAI-compat, etc. all use this.
        Anthropic uses a different stream protocol (or non-stream JSON in our loop).
        """
        return True

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
        *,
        max_tokens: int | None = None,
        thinking_level: str | None = None,
    ) -> dict[str, Any]:
        """Build the JSON request body for a chat completion call.

        Receives messages in OpenAI format with roles: system, user, assistant, tool.
        Returns provider-native body dict.
        ``max_tokens`` overrides the provider default when set.
        ``thinking_level`` is off|low|medium|high (status-bar control).
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
        # Empty async generator (no tokens).
        if False:  # pragma: no cover
            yield ""
        return


# ---------------------------------------------------------------------------
# OpenAI-compatible provider
# (works for OpenAI, DeepSeek, OpenRouter, Ollama, Google via /v1beta/openai)
# ---------------------------------------------------------------------------


class OpenAIProvider(ProviderAdapter):
    """Adapter for OpenAI and OpenAI-compatible APIs."""

    provider_name = "openai"
    default_base_url = "https://api.openai.com/v1"

    def auth_headers(self, api_key: str) -> dict[str, str]:
        # OpenAI-compat clients (and some guest gateways) require a non-empty bearer.
        key = (api_key or "").strip() or "unused"
        return {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }

    def chat_endpoint(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/chat/completions"

    # Never throttle completion length by thinking level or tool vs answer.
    MAX_TOKENS_TOOLS = MAX_OUTPUT_TOKENS
    MAX_TOKENS_ANSWER = MAX_OUTPUT_TOKENS

    def provider_max_output_tokens(self, model: str | None = None) -> int:
        """Highest completion budget this provider/model accepts.

        Override per provider when the API rejects oversized max_tokens
        (HTTP 400) — we still auto-continue on finish_reason=length.
        """
        return MAX_OUTPUT_TOKENS

    def build_body(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        *,
        max_tokens: int | None = None,
        thinking_level: str | None = None,
    ) -> dict[str, Any]:
        # Slightly lower temp with tools → fewer rambling / fake tool-call transcripts.
        temperature = 0.4 if tools else 0.6
        level = (thinking_level or "high").strip().lower()
        if level == "off":
            temperature = 0.3 if tools else 0.5
        elif level == "high":
            temperature = 0.5 if tools else 0.7
        # Always request the provider's full allowed completion budget.
        # Do not shrink for tools vs answer or thinking level.
        provider_cap = self.provider_max_output_tokens(model)
        if max_tokens is None:
            max_tokens = provider_cap
        else:
            # Honor explicit higher requests up to provider cap; never shrink below cap.
            max_tokens = max(int(max_tokens), provider_cap)
            max_tokens = min(max_tokens, provider_cap) if provider_cap > 0 else max_tokens
        # Soft system nudge for deliberation (providers without native effort API).
        msgs = list(messages)
        nudge = _thinking_nudge(level)
        if nudge:
            msgs = _prepend_system_nudge(msgs, nudge)
        body: dict[str, Any] = {
            "model": model,
            "messages": msgs,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        # Native effort when the model/API understands it (xAI/OpenAI-compat).
        if level in ("low", "medium", "high") and _model_wants_reasoning_effort(model):
            body["reasoning_effort"] = level
        return body

    def extract_response(self, response_json: dict[str, Any]) -> dict[str, Any]:
        choice = (response_json.get("choices") or [{}])[0]
        msg = choice.get("message") or choice.get("delta") or {}
        # Keep reasoning_content separately — DeepSeek thinking + tool_calls
        # requires it to be passed back on subsequent requests.
        reasoning_raw = msg.get("reasoning_content") or msg.get("reasoning") or ""
        if not isinstance(reasoning_raw, str):
            reasoning_raw = str(reasoning_raw) if reasoning_raw is not None else ""
        # Full reasoning — never slice/shorten provider thinking.
        reasoning = reasoning_raw
        content_raw = msg.get("content")
        if content_raw is None:
            content = ""
        elif isinstance(content_raw, str):
            content = content_raw
        else:
            content = str(content_raw)
        # Final answers sometimes only appear in reasoning_content.
        if not content.strip() and reasoning.strip() and not msg.get("tool_calls"):
            content = reasoning
        return {
            "content": content if content else None,
            "tool_calls": msg.get("tool_calls"),
            "reasoning_content": reasoning if reasoning else None,
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
            # Only yield final content here; reasoning is handled by the agent loop.
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

    @property
    def uses_openai_sse(self) -> bool:
        # Agent loop reads a single JSON body for Anthropic (no Anthropic SSE parser yet).
        return False

    def auth_headers(self, api_key: str) -> dict[str, str]:
        return {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "Content-Type": "application/json",
        }

    def chat_endpoint(self, base_url: str) -> str:
        return f"{base_url.rstrip('/')}/v1/messages"

    MAX_TOKENS_TOOLS = MAX_OUTPUT_TOKENS
    MAX_TOKENS_ANSWER = MAX_OUTPUT_TOKENS

    def build_body(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        *,
        max_tokens: int | None = None,
        thinking_level: str | None = None,
    ) -> dict[str, Any]:
        msgs = list(messages)
        level = (thinking_level or "high").strip().lower()
        nudge = _thinking_nudge(level)
        if nudge:
            msgs = _prepend_system_nudge(msgs, nudge)
        system_prompt, converted = self._convert_messages(msgs)
        # Full output budget always — Anthropic requires max_tokens; never throttle it.
        if max_tokens is None:
            max_tokens = MAX_OUTPUT_TOKENS
        else:
            max_tokens = max(int(max_tokens), MAX_OUTPUT_TOKENS)
        body: dict[str, Any] = {
            "model": model,
            "messages": converted,
            "max_tokens": max_tokens,
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
                raw_content = msg.get("content")
                anthropic.append(
                    {
                        "role": "user",
                        "content": AnthropicProvider._convert_user_content(raw_content),
                    }
                )

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
    def _convert_user_content(content: Any) -> str | list[dict[str, Any]]:
        """Map OpenAI string or multimodal parts to Anthropic content blocks."""
        if content is None:
            return ""
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return str(content)
        blocks: list[dict[str, Any]] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            ptype = part.get("type")
            if ptype == "text":
                text = part.get("text")
                if text is not None:
                    blocks.append({"type": "text", "text": str(text)})
            elif ptype == "image_url":
                url = ""
                image_url = part.get("image_url")
                if isinstance(image_url, dict):
                    url = str(image_url.get("url") or "")
                elif isinstance(image_url, str):
                    url = image_url
                if url.startswith("data:") and ";base64," in url:
                    header, b64 = url.split(";base64,", 1)
                    media = header[5:] if header.startswith("data:") else "image/png"
                    blocks.append(
                        {
                            "type": "image",
                            "source": {
                                "type": "base64",
                                "media_type": media or "image/png",
                                "data": b64,
                            },
                        }
                    )
                elif url:
                    blocks.append(
                        {
                            "type": "image",
                            "source": {"type": "url", "url": url},
                        }
                    )
        return blocks if blocks else ""

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
# Lightweight OpenAI-compatible specializations
# ---------------------------------------------------------------------------


class GoogleProvider(OpenAIProvider):
    """Google Gemini via the OpenAI-compatible endpoint.

    Strips request fields Gemini's OpenAI bridge may reject or ignore.
    """

    provider_name = "google"
    default_base_url = "https://generativelanguage.googleapis.com/v1beta/openai"

    def build_body(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        *,
        max_tokens: int | None = None,
        thinking_level: str | None = None,
    ) -> dict[str, Any]:
        body = super().build_body(
            model,
            messages,
            tools,
            stream,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        # Gemini OpenAI-compat is picky about some OpenAI-only knobs.
        for key in ("logit_bias", "logprobs", "top_logprobs", "n", "user", "reasoning_effort"):
            body.pop(key, None)
        # Empty tools list is invalid; omit instead.
        if not body.get("tools"):
            body.pop("tools", None)
            body.pop("tool_choice", None)
        return body


class DeepSeekProvider(OpenAIProvider):
    """DeepSeek V4 APIs (OpenAI-compatible + reasoning_content)."""

    provider_name = "deepseek"
    default_base_url = "https://api.deepseek.com"
    # DeepSeek rejects oversized max_tokens (HTTP 400). Cap to documented limits.
    # V4 Pro/Flash allow large max output; legacy chat was ~8k.
    DEEPSEEK_MAX_OUTPUT = 8192

    def provider_max_output_tokens(self, model: str | None = None) -> int:
        mid = (model or "").lower()
        # V4 and reasoner-class models support large completions.
        if (
            "v4-pro" in mid
            or "v4-flash" in mid
            or "reasoner" in mid
            or "r1" in mid
            or "think" in mid
            or "pro" in mid
        ):
            return 65_536
        return self.DEEPSEEK_MAX_OUTPUT

    def build_body(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        *,
        max_tokens: int | None = None,
        thinking_level: str | None = None,
    ) -> dict[str, Any]:
        # Cap at DeepSeek's accepted max so we don't 400 and abort the turn.
        cap = self.provider_max_output_tokens(model)
        max_tokens = cap if max_tokens is None else min(max(int(max_tokens), 1), cap)
        body = super().build_body(
            model,
            messages,
            tools,
            stream,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        # Ensure we never exceed DeepSeek's hard API limit after parent merge.
        body["max_tokens"] = min(int(body.get("max_tokens") or cap), cap)
        # Reasoner models work better with slightly lower temperature.
        if "reasoner" in (model or "").lower():
            body["temperature"] = min(float(body.get("temperature") or 0.6), 0.5)
        # DeepSeek does not use OpenAI reasoning_effort field
        body.pop("reasoning_effort", None)
        return body


class XaiProvider(OpenAIProvider):
    """xAI Grok API (OpenAI-compatible). Auth: OAuth bearer or console API key."""

    provider_name = "xai"
    default_base_url = "https://api.x.ai/v1"


# ---------------------------------------------------------------------------
# Provider registry
# ---------------------------------------------------------------------------


class GroqProvider(OpenAIProvider):
    """Groq OpenAI-compatible chat API."""

    provider_name = "groq"
    default_base_url = "https://api.groq.com/openai/v1"


class MistralProvider(OpenAIProvider):
    """Mistral OpenAI-compatible chat API."""

    provider_name = "mistral"
    default_base_url = "https://api.mistral.ai/v1"


_PROVIDERS: dict[str, type[ProviderAdapter]] = {
    "demo": OpenAIProvider,           # LLM7 guest OpenAI-compatible
    "openai": OpenAIProvider,
    "anthropic": AnthropicProvider,
    "google": GoogleProvider,
    "deepseek": DeepSeekProvider,
    "xai": XaiProvider,
    "groq": GroqProvider,
    "mistral": MistralProvider,
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
    if "deepseek" in url_lower:
        return get_provider("deepseek")
    if "api.x.ai" in url_lower or "x.ai/" in url_lower:
        return get_provider("xai")
    if "groq.com" in url_lower:
        return get_provider("groq")
    if "mistral.ai" in url_lower:
        return get_provider("mistral")
    if "openrouter" in url_lower:
        return get_provider("openrouter")
    if "generativelanguage.googleapis.com" in url_lower or "googleapis.com" in url_lower:
        return get_provider("google")
    if "11434" in url_lower or "ollama" in url_lower:
        return get_provider("ollama")
    if "openai.com" in url_lower:
        return get_provider("openai")
    return get_provider("openai")
