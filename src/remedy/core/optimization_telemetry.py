"""Phase 0 instrumentation — named counters/histograms, no new backends.

Use these helpers so later phases can prove optimization against a v0.31 baseline.
All record helpers are best-effort and never raise into callers.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager, suppress
from time import perf_counter

from remedy.core.metrics import MetricsRegistry

REGISTRY = MetricsRegistry()

# Latency names from the optimization master plan (M0.2).
LATENCY_NAMES = (
    "startup",
    "session_init",
    "llm",
    "ttfb",
    "tool",
    "memory_read",
    "memory_write",
    "event",
    "desktop_render",
    "tauri_bridge",
    "voice",
)

# Reliability names (M0.3).
RELIABILITY_COUNTERS = (
    "task_completion",
    "false_completion",
    "retry",
    "tool_failure",
    "verification_failure",
    "human_intervention",
    "aborted_task",
    "tool_call",
    "turn",
)


def observe_seconds(kind: str, seconds: float, **labels: str) -> None:
    """Record a latency sample. *kind* is one of LATENCY_NAMES."""
    with suppress(Exception):
        REGISTRY.histogram(f"remedy_{kind}_seconds", **labels).observe(float(seconds))


def inc(kind: str, amount: int = 1, **labels: str) -> None:
    """Increment a reliability counter. *kind* is one of RELIABILITY_COUNTERS."""
    with suppress(Exception):
        REGISTRY.counter(f"remedy_{kind}_total", **labels).inc(amount)


@contextmanager
def span(kind: str, **labels: str) -> Iterator[None]:
    start = perf_counter()
    try:
        yield
    finally:
        observe_seconds(kind, perf_counter() - start, **labels)


def snapshot() -> dict:
    return REGISTRY.snapshot()
