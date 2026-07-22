"""Observability: metrics collection, health checks, and counters."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class Counter:
    name: str
    value: int = 0
    labels: dict[str, str] = field(default_factory=dict)

    def inc(self, amount: int = 1) -> int:
        self.value += amount
        return self.value

    def snapshot(self) -> dict:
        return {"name": self.name, "value": self.value, "labels": self.labels}


@dataclass
class Gauge:
    name: str
    value: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)

    def set(self, value: float) -> float:
        self.value = value
        return self.value

    def snapshot(self) -> dict:
        return {"name": self.name, "value": self.value, "labels": self.labels}


@dataclass
class Histogram:
    name: str
    buckets: list[float] = field(default_factory=lambda: [0.01, 0.05, 0.1, 0.5, 1.0, 5.0, 10.0, 30.0, 60.0])
    _counts: list[int] = field(default_factory=list)
    _sum: float = 0.0
    labels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self._counts:
            self._counts = [0] * (len(self.buckets) + 1)

    def observe(self, value: float) -> None:
        self._sum += value
        for i, b in enumerate(self.buckets):
            if value <= b:
                self._counts[i] += 1
                return
        self._counts[-1] += 1

    def snapshot(self) -> dict:
        return {
            "name": self.name,
            "sum": self._sum,
            "count": sum(self._counts),
            "buckets": [
                {"le": str(b), "count": c}
                for b, c in zip(self.buckets + ["Inf"], self._counts)
            ],
            "labels": self.labels,
        }


class MetricsRegistry:
    """Thread-safe metrics registry."""

    def __init__(self) -> None:
        self._counters: dict[str, Counter] = {}
        self._gauges: dict[str, Gauge] = {}
        self._histograms: dict[str, Histogram] = {}

    def counter(self, name: str, **labels: str) -> Counter:
        key = _key(name, labels)
        if key not in self._counters:
            self._counters[key] = Counter(name=name, labels=labels)
        return self._counters[key]

    def gauge(self, name: str, **labels: str) -> Gauge:
        key = _key(name, labels)
        if key not in self._gauges:
            self._gauges[key] = Gauge(name=name, labels=labels)
        return self._gauges[key]

    def histogram(self, name: str, **labels: str) -> Histogram:
        key = _key(name, labels)
        if key not in self._histograms:
            self._histograms[key] = Histogram(name=name, labels=labels)
        return self._histograms[key]

    def snapshot(self) -> dict:
        return {
            "counters": [c.snapshot() for c in self._counters.values()],
            "gauges": [g.snapshot() for g in self._gauges.values()],
            "histograms": [h.snapshot() for h in self._histograms.values()],
        }

    @property
    def num_counters(self) -> int:
        return len(self._counters)

    @property
    def num_gauges(self) -> int:
        return len(self._gauges)

    @property
    def num_histograms(self) -> int:
        return len(self._histograms)

    def describe(self) -> list[str]:
        out: list[str] = []
        for v in self._counters.values():
            out.append(f"counter {v.name} = {v.value}")
        for v in self._gauges.values():
            out.append(f"gauge {v.name} = {v.value}")
        for v in self._histograms.values():
            out.append(f"histogram {v.name} count={sum(v._counts)} sum={v._sum:.2f}")
        return out


def _key(name: str, labels: dict[str, str]) -> str:
    if not labels:
        return name
    parts = [f"{k}={v}" for k, v in sorted(labels.items())]
    return f"{name}{{{','.join(parts)}}}"


class HealthChecker:
    """Collects health status from registered check functions."""

    def __init__(self) -> None:
        self._checks: dict[str, Any] = {}
        self._started_at = datetime.now(UTC)

    def register(self, name: str, check_fn: Any) -> None:
        self._checks[name] = check_fn

    def unregister(self, name: str) -> None:
        self._checks.pop(name, None)

    async def check(self) -> dict:
        results: dict[str, dict] = {}
        healthy = True
        for name, fn in self._checks.items():
            try:
                result = fn()
                import asyncio as aio
                if aio.iscoroutine(result):
                    result = await result
                results[name] = {"status": "ok", "detail": result}
            except Exception as e:
                results[name] = {"status": "unhealthy", "error": str(e)}
                healthy = False

        uptime = (datetime.now(UTC) - self._started_at).total_seconds()

        return {
            "status": "ok" if healthy else "degraded",
            "uptime_seconds": uptime,
            "checks": results,
            "checked_at": datetime.now(UTC).isoformat(),
        }
