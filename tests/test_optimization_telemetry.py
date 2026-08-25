"""Phase 0 instrumentation helpers."""

from __future__ import annotations

from pathlib import Path

from remedy.core.llm_log import log_llm_call
from remedy.core.optimization_telemetry import REGISTRY, inc, observe_seconds, snapshot, span
from remedy.events.bus import EventBus
from remedy.events.types import EventType


def test_observe_and_inc_show_in_snapshot():
    observe_seconds("tool", 0.05, tool="file_read")
    inc("tool_call", tool="file_read")
    with span("memory_read"):
        pass
    snap = snapshot()
    names = {c["name"] for c in snap["counters"]}
    hist = {h["name"] for h in snap["histograms"]}
    assert "remedy_tool_call_total" in names
    assert "remedy_tool_seconds" in hist
    assert "remedy_memory_read_seconds" in hist


def test_log_llm_call_observes_latency_seconds():
    before = REGISTRY.histogram(
        "remedy_llm_seconds", provider="deepseek", model="deepseek-chat"
    ).snapshot()["count"]
    log_llm_call(
        provider="deepseek",
        model="deepseek-chat",
        latency_ms=250,
        status="ok",
    )
    after = REGISTRY.histogram(
        "remedy_llm_seconds", provider="deepseek", model="deepseek-chat"
    ).snapshot()["count"]
    assert after == before + 1


def test_event_bus_emit_records_event_span(tmp_path: Path):
    before = sum(
        h["count"]
        for h in snapshot()["histograms"]
        if h["name"] == "remedy_event_seconds"
    )
    bus = EventBus(db_path=tmp_path / "events.db")
    bus.emit_simple(EventType.GOAL_STARTED, session_id="s", turn_id="t", goal="x")
    bus.close()
    after = sum(
        h["count"]
        for h in snapshot()["histograms"]
        if h["name"] == "remedy_event_seconds"
    )
    assert after >= before + 1


def test_helpers_never_raise_on_bad_labels():
    # Best-effort: odd label values must not escape to callers.
    observe_seconds("llm", 0.01, provider=None)  # type: ignore[arg-type]
    inc("turn", amount=1)
    with span("event", event_type="goal_started"):
        pass
