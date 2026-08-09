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
from urllib.parse import urlsplit

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


def coalesce_system_messages_first(
    messages: list[dict[str, Any]] | None,
) -> list[dict[str, Any]]:
    """Jinja chat templates (Qwen3 / Qwopus / etc.) require system msgs first.

    Harness often injects mid-stream system notes; llama-server --jinja then
    400s with \"System message must be at the beginning\". Merge all system
    content into one leading system message; preserve non-system order.
    """
    if not messages:
        return []
    systems: list[str] = []
    rest: list[dict[str, Any]] = []
    for m in messages:
        if not isinstance(m, dict):
            continue
        role = str(m.get("role") or "").lower()
        if role == "system":
            c = m.get("content")
            if isinstance(c, str) and c.strip():
                systems.append(c.strip())
            elif c is not None and not isinstance(c, str):
                systems.append(str(c))
            continue
        rest.append(m)
    out: list[dict[str, Any]] = []
    if systems:
        out.append({"role": "system", "content": "\n\n".join(systems)})
    out.extend(rest)
    return out


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
            # Honor explicit requests up to provider cap (do not force always-cap).
            max_tokens = max(1, int(max_tokens))
            if provider_cap > 0:
                max_tokens = min(max_tokens, provider_cap)
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


class LlamaCppProvider(OpenAIProvider):
    """Local OpenAI-compatible host (llama.cpp / Ollama / RMB).

    Local models have a fixed ``n_ctx``. Cloud-scale ``max_tokens`` (128k)
    causes rejections. Budget completion from the resolved window; never send
    ``reasoning_effort``. Lower temperature with tools for reliable tool_calls.
    """

    provider_name = "llamacpp"
    # Agent coding defaults — enough for a multi-step tool turn or file_write/edit.
    LOCAL_DEFAULT_MAX_TOKENS = 3072
    LOCAL_MAX_TOKENS_CEILING = 12288
    default_base_url = "http://127.0.0.1:8080/v1"

    def provider_max_output_tokens(self, model: str | None = None) -> int:
        return self._local_completion_budget(model)

    def _local_completion_budget(self, model: str | None = None) -> int:
        """n_predict: fraction of window for agent work (tool chains + patches)."""
        try:
            from remedy.nanoswarm.token_nanobot import (
                get_cached_context_window,
                resolve_context_window,
            )

            win = get_cached_context_window(None, model) or resolve_context_window(
                self.provider_name, model
            )
        except Exception:
            win = 0
        if win and win > 0:
            # ~1/3 of window for completion, floor 512, cap for local hosts.
            return max(512, min(self.LOCAL_MAX_TOKENS_CEILING, int(win) // 3))
        return self.LOCAL_DEFAULT_MAX_TOKENS

    def _estimate_prompt_tokens(self, messages: list[dict[str, Any]]) -> int:
        """Rough prompt size for remaining-context clamp (chars/3 heuristic)."""
        total = 0
        for m in messages or []:
            c = m.get("content")
            if isinstance(c, str):
                total += len(c)
            elif isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        total += len(part["text"])
                    else:
                        total += 64
            else:
                total += 32
            tcs = m.get("tool_calls")
            if isinstance(tcs, list):
                for tc in tcs:
                    try:
                        total += len(str(tc))
                    except Exception:
                        total += 64
        return max(1, total // 3)

    def _window_for_budget(self, model: str | None = None) -> int:
        try:
            from remedy.core.endless_context import resolve_local_window

            return int(
                resolve_local_window(
                    provider=self.provider_name,
                    model=model,
                    base_url=getattr(self, "default_base_url", None),
                )
                or 8192
            )
        except Exception:
            pass
        try:
            from remedy.nanoswarm.token_nanobot import (
                get_cached_context_window,
                resolve_context_window,
            )

            win = get_cached_context_window(None, model) or resolve_context_window(
                self.provider_name, model
            )
            return int(win) if win and int(win) > 0 else 8192
        except Exception:
            return 8192

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
        # Jinja templates (Qwen3 family etc.): system must lead before any fit
        messages = coalesce_system_messages_first(messages)
        # Endless local context: hard-fit messages+tools into fixed n_ctx
        # *before* estimating completion — tools alone can be multi-k tokens.
        win = self._window_for_budget(model)
        fit_msgs = messages
        fit_tools = tools
        try:
            from remedy.core.endless_context import fit_local_request, resolve_local_window

            win = resolve_local_window(
                provider=self.provider_name,
                model=model,
                base_url=getattr(self, "base_url", None)
                or getattr(self, "default_base_url", None),
            ) or win
            fit_msgs, fit_tools, _meta = fit_local_request(
                messages,
                tools,
                window=win,
                provider=self.provider_name,
                model=model,
                coding_bias=True,
            )
            # fit may re-inject system mid-list — re-coalesce for jinja hosts
            fit_msgs = coalesce_system_messages_first(fit_msgs)
        except Exception:
            fit_msgs, fit_tools = messages, tools

        local_cap = self._local_completion_budget(model)
        # Clamp to remaining n_ctx so prompt + completion fits (post-fit)
        est = self._estimate_prompt_tokens(fit_msgs)
        try:
            from remedy.core.endless_context import estimate_tools_tokens

            est += estimate_tools_tokens(fit_tools)
        except Exception:
            pass
        remaining = max(256, win - est - 256)
        local_cap = max(256, min(local_cap, remaining))
        req = local_cap if max_tokens is None else min(int(max_tokens), local_cap)
        body = super().build_body(
            model,
            fit_msgs,
            fit_tools,
            stream,
            max_tokens=req,
            thinking_level=thinking_level,
        )
        body["max_tokens"] = req
        body.pop("reasoning_effort", None)
        if fit_tools:
            # Structured tool calls: low temp + explicit tool_choice auto
            body["temperature"] = min(float(body.get("temperature") or 0.4), 0.15)
            body.setdefault("tool_choice", "auto")
        else:
            body.pop("tools", None)
            body.pop("tool_choice", None)
        body.setdefault("cache_prompt", True)
        # Auto-optimize: force tools + smaller n_predict on implement turns
        try:
            from remedy.core.local_agent_optimize import apply_local_body_optimize

            um = ""
            for m in reversed(fit_msgs or []):
                if isinstance(m, dict) and m.get("role") == "user":
                    c = m.get("content")
                    um = c if isinstance(c, str) else str(c or "")
                    break
            body = apply_local_body_optimize(
                body,
                provider=self.provider_name,
                model=model,
                base_url=getattr(self, "default_base_url", None),
                user_message=um,
                step_index=int(getattr(self, "_local_step_index", 0) or 0),
                history=fit_msgs if isinstance(fit_msgs, list) else None,
            )
        except Exception:
            pass
        # Local streaming is flaky under tool_choice=required (connection resets).
        # Prefer non-stream JSON for tool rounds so pseudo/native tools complete.
        if fit_tools and body.get("tools"):
            body["stream"] = False
        return body


class RmbProvider(LlamaCppProvider):
    """RMB — Remedy Muscle Bridge (managed local llama-server for agents).

    UI brand: RMB. Engine: llama.cpp. Port 8787 by default.
    Optimized for coding + multi-step tools and long sessions (harness + n_ctx).

    Automatically uses the **currently loaded GGUF stem** as the model id and
    coalesces system messages for Jinja templates — no user knobs required.
    """

    provider_name = "rmb"
    # Coding agents need headroom for multi-file patches + full file_write/edit JSON
    # (too-low ceiling truncates tool args → TOOL_ARGS_TRUNCATED / HISTORY_STUB loops).
    LOCAL_DEFAULT_MAX_TOKENS = 4096
    LOCAL_MAX_TOKENS_CEILING = 12288
    default_base_url = "http://127.0.0.1:8787/v1"

    def _live_gguf_stem(self) -> str | None:
        try:
            from pathlib import Path

            from remedy.runtime.rmb.config import load_rmb_json, merge_state

            st = merge_state(load_rmb_json())
            mp = str(st.get("model_path") or "").strip()
            if mp:
                return Path(mp).stem
            mid = str(st.get("model_id") or "").strip()
            return mid or None
        except Exception:
            return None

    def _local_completion_budget(self, model: str | None = None) -> int:
        """Prefer live RMB ctx_size when known (endless sessions need accurate fill%)."""
        try:
            from remedy.runtime.rmb.config import load_rmb_json, merge_state
            from remedy.nanoswarm.token_nanobot import (
                cache_context_window,
                get_cached_context_window,
            )

            st = merge_state(load_rmb_json())
            ctx = int(st.get("ctx_size") or 0)
            if ctx >= 2048:
                cache_context_window(st.get("base_url"), model, ctx)
            hit = get_cached_context_window(st.get("base_url"), model)
            if hit:
                return max(512, min(self.LOCAL_MAX_TOKENS_CEILING, int(hit) // 3))
        except Exception:
            pass
        return super()._local_completion_budget(model)

    def _window_for_budget(self, model: str | None = None) -> int:
        try:
            from remedy.core.endless_context import resolve_local_window

            return int(
                resolve_local_window(
                    provider="rmb",
                    model=model,
                    base_url=self.default_base_url,
                )
                or 8192
            )
        except Exception:
            pass
        try:
            from remedy.runtime.rmb.config import load_rmb_json, merge_state

            st = merge_state(load_rmb_json())
            ctx = int(st.get("ctx_size") or 0)
            if ctx >= 2048:
                return ctx
        except Exception:
            pass
        return super()._window_for_budget(model)

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
        # Wake if down; if already starting/loading, leave wait to the ReAct
        # 503 path (wait_rmb_ready) so build_body stays non-blocking.
        # force=True bypasses the short running-cache so a just-died host
        # is not reported healthy for ~1.5s after child exit.
        try:
            from remedy.runtime.rmb.service import (
                ensure_rmb_watchdog,
                is_loading,
                is_running,
                is_starting,
                loading_stalled,
                wake_rmb_async,
            )

            ensure_rmb_watchdog()
            if not is_running(force=True, require_http=True):
                if loading_stalled():
                    # Wedged mid-load — fire async restart (wait_rmb_ready will sync)
                    wake_rmb_async()
                elif not is_starting() and not is_loading():
                    wake_rmb_async()
        except Exception:
            pass
        # Always use the Loaded GGUF stem — status bar / session may lag
        live = self._live_gguf_stem()
        use_model = live or model
        body = super().build_body(
            use_model,
            messages,
            tools,
            stream,
            max_tokens=max_tokens,
            thinking_level=thinking_level,
        )
        body["model"] = use_model
        # Jinja templates (Qwen3 / Qwopus): system messages must lead
        if isinstance(body.get("messages"), list):
            body["messages"] = coalesce_system_messages_first(body["messages"])
        # super already ran endless fit + local optimize
        if body.get("tools"):
            body["temperature"] = min(float(body.get("temperature") or 0.1), 0.1)
            # Preserve tool_choice=required from local optimize when set
            if body.get("tool_choice") not in ("required", "any"):
                body["tool_choice"] = body.get("tool_choice") or "auto"
        # Strip internal meta so llama-server never sees it
        body.pop("_remedy_endless", None)
        body.pop("_remedy_local", None)
        return body


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
    "poe": OpenAIProvider,            # Poe OpenAI-compatible (api.poe.com/v1)
    "ollama": LlamaCppProvider,       # Ollama talks llama.cpp OpenAI-compat
    "llamacpp": LlamaCppProvider,     # bundled llama-server (vision/nano runtime)
    "rmb": RmbProvider,               # external Remedy Muscle Bridge host
    "custom": OpenAIProvider,         # Unknown custom endpoints default to OpenAI-compatible
}


def get_provider(provider_name: str) -> ProviderAdapter:
    """Return a provider adapter instance for the named provider.

    Falls back to OpenAI-compatible for unknown providers.
    """
    cls = _PROVIDERS.get(provider_name.lower(), OpenAIProvider)
    return cls()


def _is_loopback_base_url(url: str) -> bool:
    """True for local endpoints (localhost / 127.x / [::1]) and KoboldCpp."""
    try:
        host = (urlsplit(url).hostname or "").lower()
    except ValueError:
        host = ""
    if "kobold" in url.lower():
        return True
    return host in ("localhost", "0.0.0.0", "::1") or host.startswith("127.")


def _is_rmb_base_url(url: str) -> bool:
    """True for Remedy Muscle Bridge host (port 8787 / dedicated hostnames)."""
    try:
        from remedy.runtime.rmb.mode import is_rmb_base_url

        return is_rmb_base_url(url)
    except Exception:
        u = (url or "").lower()
        if not u:
            return False
        try:
            port = urlsplit(u).port
        except ValueError:
            port = None
        return port == 8787 or ":8787" in u


def select_provider(provider_name: str | None, base_url: str = "") -> ProviderAdapter:
    """Pick an adapter from a provider name plus its base URL.

    ``custom`` (or unknown names) point at OpenAI-compatible endpoints that
    could be either cloud proxies or local servers. Local servers (llama.cpp /
    Ollama / RMB / KoboldCpp) have a small fixed ``n_ctx`` and must not receive
    a cloud-scale ``max_tokens`` — resolve them to the local adapter via the
    base URL so completion stays within the physical window.
    """
    name = (provider_name or "openai").lower() or "openai"
    if name == "rmb":
        return get_provider("rmb")
    if name == "custom" or name not in _PROVIDERS:
        if base_url.strip():
            return get_provider_for_base_url(base_url)
        return get_provider(name)
    # Any named provider on loopback → local adapter (avoid cloud max_tokens)
    if base_url.strip() and _is_loopback_base_url(base_url.lower()):
        if _is_rmb_base_url(base_url):
            return get_provider("rmb")
        if name == "ollama":
            return get_provider("ollama")
        return get_provider("llamacpp")
    return get_provider(name)


def get_provider_for_base_url(base_url: str) -> ProviderAdapter:
    """Heuristically detect the provider from the base URL."""
    url_lower = base_url.lower()
    if _is_rmb_base_url(url_lower):
        return get_provider("rmb")
    if _is_loopback_base_url(url_lower):
        return get_provider("llamacpp")
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
    if "api.poe.com" in url_lower or "poe.com" in url_lower:
        return get_provider("poe")
    if "generativelanguage.googleapis.com" in url_lower or "googleapis.com" in url_lower:
        return get_provider("google")
    if "11434" in url_lower or "ollama" in url_lower:
        return get_provider("ollama")
    if "openai.com" in url_lower:
        return get_provider("openai")
    return get_provider("openai")
