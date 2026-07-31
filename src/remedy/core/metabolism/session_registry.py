"""Bounded session-scoped registries for metabolism organs.

Long-lived ``remedy serve`` processes create per-session ledgers/maps without
always calling reset_*. Cap insertion-order maps so memory cannot grow without
bound when sessions churn.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TypeVar

T = TypeVar("T")

# Soft ceiling for in-process metabolism maps (evidence, governor, …).
MAX_SESSION_ENTRIES = 96


def registry_get[T](
    store: dict[str, T],
    key: str,
    factory: Callable[[], T],
    *,
    max_entries: int = MAX_SESSION_ENTRIES,
) -> T:
    """LRU-ish get: touch existing key; create + FIFO-evict when over cap."""
    if key in store:
        # Move to end (recency) for FIFO-from-front eviction
        val = store.pop(key)
        store[key] = val
        return val
    val = factory()
    store[key] = val
    while len(store) > max_entries:
        oldest = next(iter(store))
        if oldest == key:
            break
        store.pop(oldest, None)
    return val
