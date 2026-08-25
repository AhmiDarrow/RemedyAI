"""Phase 0 instrumentation helpers."""

from __future__ import annotations

from remedy.core.optimization_telemetry import inc, observe_seconds, snapshot, span


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
