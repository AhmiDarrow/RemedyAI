"""Health nanobot — provider latency / error / 429 signals (local only).

Feeds Continuity dashboard and optional soft system notes after flaky runs.
Does not probe the network itself — agent/runtime reports outcomes.
"""

from __future__ import annotations

import threading
import time
from collections import deque
from typing import Any


class HealthNanobot:
    """Sliding window of provider call outcomes per provider|model."""

    def __init__(self, window: int = 32) -> None:
        self.window = window
        self._lock = threading.Lock()
        # key -> deque of {ts, ok, latency_ms, code}
        self._events: dict[str, deque[dict[str, Any]]] = {}
        self.reports = 0

    def _key(self, provider: str | None, model: str | None) -> str:
        return f"{(provider or '').lower()}|{(model or '').lower()}"

    def report(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        ok: bool = True,
        latency_ms: float = 0.0,
        error: str | None = None,
        status_code: int | None = None,
    ) -> dict[str, Any]:
        key = self._key(provider, model)
        err = (error or "").lower()
        code = status_code
        if code is None:
            if "429" in err or "rate limit" in err:
                code = 429
            elif "401" in err or "unauthorized" in err:
                code = 401
            elif "timeout" in err or "timed out" in err:
                code = 408
            elif "503" in err or "502" in err or "overloaded" in err:
                code = 503
        ev = {
            "ts": time.time(),
            "ok": bool(ok),
            "latency_ms": float(latency_ms or 0),
            "code": code,
        }
        with self._lock:
            buf = self._events.setdefault(key, deque(maxlen=self.window))
            buf.append(ev)
            self.reports += 1
        return self.snapshot(provider=provider, model=model)

    def snapshot(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
    ) -> dict[str, Any]:
        key = self._key(provider, model)
        with self._lock:
            buf = list(self._events.get(key) or [])
        n = len(buf)
        if not n:
            return {
                "bot": "health",
                "provider": (provider or "").lower() or None,
                "model": model,
                "samples": 0,
                "error_rate": 0.0,
                "rate_limit_hits": 0,
                "avg_latency_ms": None,
                "flaky": False,
                "system_hint": "",
            }
        errs = sum(1 for e in buf if not e.get("ok"))
        rlim = sum(1 for e in buf if e.get("code") == 429)
        lats = [float(e.get("latency_ms") or 0) for e in buf if e.get("latency_ms")]
        avg = (sum(lats) / len(lats)) if lats else None
        err_rate = errs / n
        flaky = err_rate >= 0.35 or rlim >= 2 or (avg is not None and avg >= 45_000)
        hint = ""
        if rlim >= 1:
            hint = (
                "[Continuity/Health] Provider rate-limit signals recently — "
                "slow down, retry later, or switch model/provider."
            )
        elif flaky:
            hint = (
                "[Continuity/Health] Provider calls look flaky "
                f"(error_rate={err_rate:.0%}"
                + (f", avg_latency_ms={avg:.0f}" if avg else "")
                + "). Prefer smaller tool batches or a fallback provider."
            )
        return {
            "bot": "health",
            "provider": (provider or "").lower() or None,
            "model": model,
            "samples": n,
            "error_rate": round(err_rate, 3),
            "rate_limit_hits": rlim,
            "avg_latency_ms": round(avg, 1) if avg is not None else None,
            "flaky": flaky,
            "system_hint": hint,
        }

    def failover_suggestion(
        self,
        *,
        provider: str | None = None,
        model: str | None = None,
        connected_providers: list[str] | None = None,
    ) -> dict[str, Any]:
        """If current provider is flaky, suggest another connected provider."""
        snap = self.snapshot(provider=provider, model=model)
        out: dict[str, Any] = {
            "flaky": bool(snap.get("flaky")),
            "suggest_switch": False,
            "suggested_provider": None,
            "reason": None,
            "health": snap,
        }
        if not snap.get("flaky"):
            return out
        cur = (provider or "").strip().lower()
        connected = [
            str(p).strip().lower()
            for p in (connected_providers or [])
            if str(p).strip()
        ]
        # Prefer free/local when flaky paid provider
        preference = ["ollama", "demo", "openrouter", "groq", "google", "deepseek", "openai", "xai", "anthropic"]
        candidates = [p for p in preference if p in connected and p != cur]
        if not candidates:
            candidates = [p for p in connected if p != cur]
        if candidates:
            out["suggest_switch"] = True
            out["suggested_provider"] = candidates[0]
            if snap.get("rate_limit_hits"):
                out["reason"] = (
                    f"Rate limits on {cur or 'current'} — try {candidates[0]}"
                )
            else:
                out["reason"] = (
                    f"Unstable responses from {cur or 'current'} — try {candidates[0]}"
                )
        else:
            out["reason"] = (
                "Provider looks flaky; add another provider in Settings or retry later."
            )
        return out

    def status(self) -> dict[str, Any]:
        with self._lock:
            keys = list(self._events.keys())
            total = sum(len(v) for v in self._events.values())
        return {
            "bot": "health",
            "buckets": len(keys),
            "events": total,
            "reports": self.reports,
            "providers": keys[:20],
        }
