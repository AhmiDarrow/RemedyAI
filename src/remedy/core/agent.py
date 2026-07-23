"""Concrete agent runtime -- BasicRuntime with LLM integration and ReAct tool use.

Provides the default Remedy agent: a multi-step ReAct loop that stores conversation
in memory, calls LLM providers through the adapter layer, and invokes tools
through the ToolRegistry.
"""

from __future__ import annotations

import json
import logging
from collections.abc import AsyncIterator
from contextlib import suppress
from typing import Any
from uuid import uuid4

import aiohttp

from remedy.core.providers import ProviderAdapter, get_provider
from remedy.core.runtime import AgentRuntime
from remedy.memory.store import MemoryStore
from remedy.models import (
    AgentConfig,
    ChannelKind,
    EventKind,
    GatewayEvent,
    ToolCall,
    ToolResult,
)
from remedy.skills.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = (
    "You are Remedy, a helpful AI agent assistant. You have access to tools and memory.\n\n"
    "Use the conversation history and tools to help the user accomplish their tasks.\n"
    "Be concise and direct in your responses."
)

_MAX_REACT_STEPS = 6


class BasicRuntime(AgentRuntime):
    """Default concrete agent runtime with LLM integration and tool support.

    Features:
    - Processes gateway events with conversation memory
    - Multi-provider LLM integration via provider adapters
    - Multi-step ReAct tool loop when tools are registered
    - Streaming and non-streaming response modes
    - Falls back to echo-style responses when no LLM is configured
    """

    def __init__(self, config: AgentConfig, memory: MemoryStore | None = None) -> None:
        super().__init__(config, memory=memory)
        self.tool_registry = ToolRegistry()
        self._system_prompt = _DEFAULT_SYSTEM_PROMPT
        self._llm_api_key: str = config.llm_api_key
        self._llm_model: str = config.llm_model
        self._llm_base_url: str = config.llm_base_url or "https://api.openai.com/v1"
        self._llm_provider: str = getattr(config, "llm_provider", "openai") or "openai"
        self._provider: ProviderAdapter = get_provider(self._llm_provider)
        self._max_react_steps = _MAX_REACT_STEPS

    def reconfigure_llm(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = None,
    ) -> None:
        """Hot-apply LLM settings so changes persist without restarting the server."""
        if provider is not None and provider.strip():
            self._llm_provider = provider.strip().lower()
            self._provider = get_provider(self._llm_provider)
            if hasattr(self, "config") and self.config is not None:
                try:
                    self.config.llm_provider = self._llm_provider
                except Exception:
                    pass
        if model is not None and model.strip():
            self._llm_model = model.strip()
            if hasattr(self, "config") and self.config is not None:
                try:
                    self.config.llm_model = self._llm_model
                except Exception:
                    pass
        if base_url is not None and base_url.strip():
            self._llm_base_url = base_url.strip()
            if hasattr(self, "config") and self.config is not None:
                try:
                    self.config.llm_base_url = self._llm_base_url
                except Exception:
                    pass
        if api_key is not None:
            # Empty string means leave unchanged (UI "keep current" path).
            if api_key != "":
                self._llm_api_key = api_key
                if hasattr(self, "config") and self.config is not None:
                    try:
                        self.config.llm_api_key = self._llm_api_key
                    except Exception:
                        pass

    async def handle_event(self, event: GatewayEvent) -> AsyncIterator[Any]:
        kind = event.kind.value if hasattr(event.kind, "value") else str(event.kind)

        if kind in ("heartbeat",):
            return

        yield f"[{self.config.name}] Processing {event.kind.value} from {event.channel.value}"

        message = event.payload.get("message", "")
        if not message:
            return

        if event.session_id:
            self._session_id = event.session_id

        await self.remember(
            content=f"User ({event.source_id}): {message}",
            title=f"Message from {event.source_id}",
            importance=0.5,
        )

        response = await self._generate_response(message, event)

        if response:
            await self.remember(
                content=f"Remedy: {response}",
                title="Agent response",
                importance=0.4,
            )
            yield response

    async def call_tool(self, tool_call: ToolCall) -> ToolResult:
        name = tool_call.tool_name
        try:
            result = await self.tool_registry.execute(name, **tool_call.arguments)
            return ToolResult(
                call_id=tool_call.id,
                success=True,
                data=result,
            )
        except ValueError as e:
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=str(e),
            )
        except Exception as e:
            logger.exception("Tool %s failed", name)
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=str(e),
            )

    async def _generate_response(
        self,
        message: str,
        event: GatewayEvent,
    ) -> str:
        if self._llm_api_key:
            return await self._call_llm(message)
        return (
            f"[FALLBACK MODE — No API key configured]\n\n"
            f"{self._fallback_response(message, event)}"
        )

    def _openai_tools(self) -> list[dict[str, Any]]:
        tools: list[dict[str, Any]] = []
        for t in self.tool_registry.tools:
            params = t.parameters if t.parameters else {"type": "object", "properties": {}}
            if "type" not in params:
                params = {"type": "object", "properties": params}
            tools.append(
                {
                    "type": "function",
                    "function": {
                        "name": t.name,
                        "description": t.description or t.name,
                        "parameters": params,
                    },
                }
            )
        return tools

    async def _call_llm(self, message: str) -> str:
        """Call the LLM with ReAct tool-use loop (non-streaming)."""
        full = ""
        try:
            async for chunk in self._call_llm_stream(message):
                full += chunk
            return full
        except Exception as e:
            logger.exception("LLM call failed")
            return f"\n[LLM EXCEPTION]\n{e}\n[END LLM EXCEPTION]"

    async def _call_llm_stream(
        self, message: str
    ) -> AsyncIterator[str]:
        """Call the LLM with ReAct tool-use loop, yielding tokens as they arrive.

        Yields status tokens prefixed with '@@' for tool-call lifecycle events.
        """
        try:
            context = await self._build_context()
            messages: list[dict[str, Any]] = [
                {"role": "system", "content": self._system_prompt + "\n\n" + context},
                {"role": "user", "content": message},
            ]
            tools = self._openai_tools()

            for step in range(self._max_react_steps):
                body = self._provider.build_body(
                    model=self._llm_model,
                    messages=messages,
                    tools=tools or None,
                    stream=True,
                )
                headers = self._provider.auth_headers(self._llm_api_key)
                endpoint = self._provider.chat_endpoint(self._llm_base_url)

                collected: dict[str, Any] = {"content": None, "tool_calls": None}
                tool_call_acc: dict[int, dict[str, Any]] = {}

                async with (
                    aiohttp.ClientSession() as session,
                    session.post(
                        endpoint,
                        headers=headers,
                        json=body,
                        timeout=aiohttp.ClientTimeout(total=120),
                    ) as resp,
                ):
                    if resp.status != 200:
                        text = await resp.text()
                        logger.error("LLM API error %d: %s", resp.status, text[:500])
                        yield f"\n[LLM ERROR — HTTP {resp.status}]\n{text[:500]}\n[END LLM ERROR]"
                        return

                    if self._provider.provider_name == "openai":
                        has_tool_calls = False
                        async for line in resp.content:
                            line_text = line.decode("utf-8").strip()
                            if not line_text or line_text.startswith(":"):
                                continue
                            if line_text == "data: [DONE]":
                                break
                            if line_text.startswith("data: "):
                                line_text = line_text[6:]
                            try:
                                chunk = json.loads(line_text)
                            except json.JSONDecodeError:
                                continue
                            choice = (chunk.get("choices") or [{}])[0]
                            delta = choice.get("delta") or {}
                            content_delta = delta.get("content")
                            if content_delta:
                                yield content_delta
                            tc_deltas = delta.get("tool_calls") or []
                            for tc in tc_deltas:
                                idx = tc.get("index", 0)
                                if idx not in tool_call_acc:
                                    tool_call_acc[idx] = {
                                        "id": tc.get("id") or "",
                                        "type": "function",
                                        "function": {
                                            "name": (
                                                tc.get("function", {}).get("name")
                                                or ""
                                            ),
                                            "arguments": (
                                                tc.get("function", {}).get(
                                                    "arguments"
                                                )
                                                or ""
                                            ),
                                        },
                                    }
                                else:
                                    acc = tool_call_acc[idx]
                                    fn_args = (
                                        tc.get("function", {}).get("arguments")
                                        or ""
                                    )
                                    if fn_args:
                                        acc["function"]["arguments"] += fn_args
                            finish = choice.get("finish_reason")
                            if finish == "tool_calls":
                                has_tool_calls = True
                                yield "@@tool_calls"
                        if has_tool_calls:
                            yield "@@tool_calls"
                    else:
                        data = await resp.json()
                        parsed = self._provider.extract_response(data)
                        content = parsed.get("content")
                        tool_calls_list_raw = parsed.get("tool_calls")
                        if content:
                            yield content
                        if tool_calls_list_raw:
                            tool_call_acc = dict(enumerate(tool_calls_list_raw))
                            yield "@@tool_calls"
                        collected = parsed

                tool_calls_list = (
                    list(tool_call_acc.values())
                    if tool_call_acc
                    else collected.get("tool_calls")
                )

                if not tool_calls_list:
                    return

                messages.append(
                    {
                        "role": "assistant",
                        "content": collected.get("content"),
                        "tool_calls": tool_calls_list,
                    }
                )
                for tc in tool_calls_list:
                    fn = tc.get("function") or {}
                    name = fn.get("name") or ""
                    raw_args = fn.get("arguments") or "{}"
                    try:
                        args = (
                            json.loads(raw_args)
                            if isinstance(raw_args, str)
                            else dict(raw_args)
                        )
                    except json.JSONDecodeError:
                        args = {}
                    if not isinstance(args, dict):
                        args = {}

                    yield f"@@tool_call:{name}"

                    result = await self.call_tool(
                        ToolCall(tool_name=name, arguments=args)
                    )
                    payload = (
                        result.data
                        if result.success
                        else {"error": result.error or "tool failed"}
                    )
                    content_str = (
                        payload
                        if isinstance(payload, str)
                        else json.dumps(payload, default=str)
                    )
                    messages.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.get("id") or str(uuid4()),
                            "content": content_str,
                        }
                    )
                    yield f"@@tool_result:{name}"

                logger.debug(
                    "ReAct step %d executed %d tool call(s)",
                    step + 1,
                    len(tool_calls_list),
                )

            yield "[Reached maximum tool-use steps without a final answer]"
        except Exception as e:
            logger.exception("LLM stream failed")
            yield f"\n[LLM STREAM EXCEPTION]\n{e}\n[END LLM STREAM EXCEPTION]"

    async def _post_chat(
        self, body: dict[str, Any]
    ) -> dict[str, Any] | str:
        headers = self._provider.auth_headers(self._llm_api_key)
        endpoint = self._provider.chat_endpoint(self._llm_base_url)

        async with (
            aiohttp.ClientSession() as session,
            session.post(
                endpoint,
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=60),
            ) as resp,
        ):
            if resp.status != 200:
                text = await resp.text()
                logger.error("LLM API error %d: %s", resp.status, text[:500])
                return f"\n[LLM ERROR — HTTP {resp.status}]\n{text[:500]}\n[END LLM ERROR]"
            return await resp.json()

    async def stream_response(
        self, message: str, session_id: str | None = None
    ) -> AsyncIterator[str]:
        """Stream tokens from the LLM for real-time SSE delivery.

        Yields individual tokens as they arrive from the provider.
        Tool-call lifecycle events are prefixed with '@@'.
        Falls back to the echo-style fallback when no API key is configured.
        """
        if not self._llm_api_key:
            yield "[FALLBACK MODE — No API key configured. Set REMEDY_LLM_API_KEY or add llm_api_key to ~/.remedy/config.toml]\n\n"
            full = self._fallback_response(
                message,
                GatewayEvent(
                    kind=EventKind.MESSAGE,
                    channel=ChannelKind.WEB,
                    source_id="stream",
                    payload={"message": message},
                    session_id=session_id,
                ),
            )
            yield full
            return

        async for chunk in self._call_llm_stream(message):
            yield chunk

    async def _build_context(self) -> str:
        parts = []
        recent: list[Any] = []
        with suppress(Exception):
            recent = await self.memory.list_recent(limit=20)
        if recent:
            lines = []
            for e in recent:
                ts = e.created_at.isoformat()[:19] if e.created_at else "?"
                lines.append(f"[{ts}] {e.content[:200]}")
            parts.append("Recent memory:\n" + "\n".join(lines))

        tools = self.tool_registry.tools
        if tools:
            names = ", ".join(t.name for t in tools)
            parts.append(f"Available tools: {names}")

        return "\n\n".join(parts)

    def _fallback_response(self, message: str, event: GatewayEvent) -> str:
        msg_lower = message.lower().strip()

        greetings = {"hello", "hi", "hey", "greetings", "yo"}
        words = set(msg_lower.rstrip("!.,?").split())
        if msg_lower in greetings or words & greetings:
            return f"Hello! I'm {self.config.name}. How can I help you?"

        if "help" in msg_lower or "?" in msg_lower:
            return (
                "I'm a basic agent runtime. I can remember conversations in my "
                "persistent store. Try using memory commands or tools if available."
            )

        if "remember" in msg_lower or "memory" in msg_lower:
            return "I've stored our conversation in memory. I can recall it later if needed."

        return (
            f"Received: {message[:200]}. "
            f"I'm running in fallback mode. Set an LLM API key (via config or "
            f"REMEDY_LLM_API_KEY env var) for intelligent responses."
        )
