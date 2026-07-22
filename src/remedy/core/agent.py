"""Concrete agent runtime -- BasicRuntime with LLM integration and ReAct-style tool use.

Provides the default Remedy agent: a ReAct-loop runner that stores conversation
in memory, calls an OpenAI-compatible LLM when configured, and invokes tools
through the ToolRegistry.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from typing import Any

import aiohttp

from remedy.core.runtime import AgentRuntime
from remedy.memory.store import MemoryStore
from remedy.models import (
    AgentConfig,
    GatewayEvent,
    ToolCall,
    ToolResult,
)
from remedy.skills.tool_registry import ToolRegistry

logger = logging.getLogger(__name__)

_DEFAULT_SYSTEM_PROMPT = """You are Remedy, a helpful AI agent assistant. You have access to tools and memory.

Use the conversation history and tools to help the user accomplish their tasks.
Be concise and direct in your responses."""


class BasicRuntime(AgentRuntime):
    """Default concrete agent runtime with LLM integration and tool support.

    Features:
    - Processes gateway events with conversation memory
    - OpenAI-compatible LLM integration (via aiohttp)
    - Tool invocation through ToolRegistry
    - Falls back to echo-style responses when no LLM is configured
    """

    def __init__(self, config: AgentConfig, memory: MemoryStore | None = None) -> None:
        super().__init__(config, memory=memory)
        self.tool_registry = ToolRegistry()
        self._system_prompt = _DEFAULT_SYSTEM_PROMPT
        self._llm_api_key: str = config.llm_api_key
        self._llm_model: str = config.llm_model
        self._llm_base_url: str = config.llm_base_url or "https://api.openai.com/v1"

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
        tool = self.tool_registry.get(name)

        if tool is None:
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=f"Tool not found: {name}",
            )

        try:
            result = await tool.execute(**tool_call.arguments)
            return ToolResult(
                call_id=tool_call.id,
                success=True,
                data=result,
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
        return self._fallback_response(message, event)

    async def _call_llm(self, message: str) -> str:
        try:
            context = await self._build_context()
            headers = {
                "Authorization": f"Bearer {self._llm_api_key}",
                "Content-Type": "application/json",
            }
            body = {
                "model": self._llm_model,
                "messages": [
                    {"role": "system", "content": self._system_prompt + "\n\n" + context},
                    {"role": "user", "content": message},
                ],
                "temperature": 0.7,
                "max_tokens": 1024,
            }

            async with aiohttp.ClientSession() as session, session.post(
                f"{self._llm_base_url}/chat/completions",
                headers=headers,
                json=body,
                timeout=aiohttp.ClientTimeout(total=30),
            ) as resp:
                if resp.status != 200:
                    text = await resp.text()
                    logger.error("LLM API error %d: %s", resp.status, text[:200])
                    return f"[LLM error: {resp.status}]"

                data = await resp.json()
                choice = data.get("choices", [{}])[0]
                return choice.get("message", {}).get("content", "") or ""
        except Exception as e:
            logger.exception("LLM call failed")
            return f"[LLM error: {e}]"

    async def _build_context(self) -> str:
        parts = []

        recent = await self.recall("", limit=20)
        if recent:
            lines = []
            for e in recent:
                ts = e.created_at.isoformat()[:19] if e.created_at else "?"
                lines.append(f"[{ts}] {e.content[:200]}")
            parts.append("Recent memory:\n" + "\n".join(lines))

        tools = self.tool_registry.list()
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
                "I'm a basic agent runtime. I can remember conversations in my persistent store. "
                "Try using memory commands or tools if available."
            )

        if "remember" in msg_lower or "memory" in msg_lower:
            return "I've stored our conversation in memory. I can recall it later if needed."

        return (
            f"Received: {message[:200]}. "
            f"I'm running in fallback mode. Set an LLM API key (via config or "
            f"REMEDY_LLM_API_KEY env var) for intelligent responses."
        )
