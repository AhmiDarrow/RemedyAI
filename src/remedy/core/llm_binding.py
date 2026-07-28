"""Per-turn LLM binding via ContextVar (safe multi-session / multi-provider).

The agent runtime is a process singleton. Concurrent turns must not share
``runtime._llm_*`` mid-flight. Bindings are set at turn start and read from
context for every HTTP call.
"""

from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class LlmBinding:
    provider: str
    model: str
    base_url: str
    api_key: str

    def adapter(self) -> Any:
        from remedy.core.providers import get_provider

        return get_provider(self.provider or "openai")


_llm_binding: ContextVar[LlmBinding | None] = ContextVar(
    "remedy_llm_binding", default=None
)


def set_llm_binding(bind: LlmBinding) -> Token:
    return _llm_binding.set(bind)


def reset_llm_binding(token: Token) -> None:
    _llm_binding.reset(token)


def get_llm_binding(runtime: Any | None = None) -> LlmBinding:
    """Current turn binding, or snapshot from runtime (legacy / no turn)."""
    cur = _llm_binding.get()
    if cur is not None:
        return cur
    if runtime is None:
        return LlmBinding(provider="openai", model="", base_url="", api_key="")
    return LlmBinding(
        provider=str(getattr(runtime, "_llm_provider", None) or "openai"),
        model=str(getattr(runtime, "_llm_model", None) or ""),
        base_url=str(getattr(runtime, "_llm_base_url", None) or ""),
        api_key=str(getattr(runtime, "_llm_api_key", None) or ""),
    )


def binding_from_runtime(runtime: Any) -> LlmBinding:
    return get_llm_binding(runtime)
