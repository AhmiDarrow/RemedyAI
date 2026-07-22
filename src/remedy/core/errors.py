"""Consistent error types and retry handling for Remedy."""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable, Coroutine
from typing import Any, TypeVar

T = TypeVar("T")
logger = logging.getLogger(__name__)


class RemedyError(Exception):
    """Base exception for all Remedy-specific errors."""

    def __init__(self, message: str, code: str | None = None, details: dict | None = None) -> None:
        super().__init__(message)
        self.code = code or "INTERNAL_ERROR"
        self.details = details or {}
        self.timestamp = time.time()


class ConfigError(RemedyError):
    """Configuration-related errors."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code="CONFIG_ERROR", details=kwargs)


class SkillError(RemedyError):
    """Skill loading/validation/execution errors."""

    def __init__(self, message: str, skill_name: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, code="SKILL_ERROR", details=dict(skill_name=skill_name, **kwargs))


class MemoryError(RemedyError):
    """Memory store errors."""

    def __init__(self, message: str, **kwargs: Any) -> None:
        super().__init__(message, code="MEMORY_ERROR", details=kwargs)


class GatewayError(RemedyError):
    """Gateway/routing errors."""

    def __init__(self, message: str, channel: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, code="GATEWAY_ERROR", details=dict(channel=channel, **kwargs))


class ExecutionError(RemedyError):
    """Tool/sandbox execution errors."""

    def __init__(self, message: str, tool_name: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, code="EXECUTION_ERROR", details=dict(tool_name=tool_name, **kwargs))


class SecurityError(RemedyError):
    """Security/policy violations."""

    def __init__(self, message: str, rule: str | None = None, **kwargs: Any) -> None:
        super().__init__(message, code="SECURITY_ERROR", details=dict(rule=rule, **kwargs))


class APIRetryPolicy:
    def __init__(
        self,
        name: str,
        condition: str,
        max_retries: int = 3,
        base_delay: float = 1.0,
        max_delay: float = 60.0,
        backoff_multiplier: float = 2.0,
        jitter: bool = True,
    ) -> None:
        self.name = name
        self.condition = condition
        self.max_retries = max_retries
        self.base_delay = base_delay
        self.max_delay = max_delay
        self.backoff_multiplier = backoff_multiplier
        self.jitter = jitter

    def should_retry(self, error: Exception) -> bool:
        """Check if the error matches this policy's condition."""
        if self.condition == "connection_error":
            return isinstance(error, (ConnectionError, TimeoutError, OSError, asyncio.TimeoutError))
        if self.condition == "rate_limit":
            msg = str(error).lower()
            return any(kw in msg for kw in ("rate limit", "too many requests", "429", "503"))
        if self.condition == "all":
            return True
        return False

    def delay_for_attempt(self, attempt: int) -> float:
        delay = min(self.base_delay * (self.backoff_multiplier ** attempt), self.max_delay)
        if self.jitter:
            import random
            delay *= 0.5 + random.random()
        return delay

    async def execute(
        self,
        func: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
        **kwargs: Any,
    ) -> T:
        """Execute with retry logic."""
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            try:
                return await func(*args, **kwargs)
            except Exception as e:
                last_error = e
                if not self.should_retry(e) or attempt >= self.max_retries:
                    raise

                delay = self.delay_for_attempt(attempt)
                logger.warning(
                    "Retry %s attempt %d/%d in %.1fs: %s",
                    self.name, attempt + 1, self.max_retries,
                    delay, e,
                )
                await asyncio.sleep(delay)

        raise last_error  # type: ignore[misc]
