"""Metrics registry + agency rollup tests (no FastAPI /api/metrics twin)."""

from __future__ import annotations

import asyncio

from remedy.core.metrics import (
    HealthChecker,
    MetricsRegistry,
    agency_metrics_rollup,
    default_registry,
)


def test_prometheus_text_format() -> None:
    reg = MetricsRegistry()
    reg.counter("http_requests_total", method="GET").inc(3)
    reg.gauge("queue_depth").set(1.5)
    reg.histogram("latency_seconds").observe(0.02)
    text = reg.prometheus_text()
    assert "# TYPE http_requests_total counter" in text
    assert 'http_requests_total{method="GET"} 3' in text
    assert "# TYPE queue_depth gauge" in text
    assert "queue_depth 1.5" in text
    assert "latency_seconds_bucket" in text
    assert "latency_seconds_count" in text


def test_agency_rollup_sums_recovery_and_skill_counters() -> None:
    reg = MetricsRegistry()
    reg.counter("remedy_tool_recovery_nudge_total", kind="tool_error").inc(2)
    reg.counter("remedy_tool_batch_errors_total").inc()
    reg.counter("remedy_skill_run_total", status="ok").inc()
    reg.counter("remedy_skill_run_total", status="error").inc()
    agency = agency_metrics_rollup(reg.snapshot())
    assert agency["tool_recovery_nudges"] >= 2
    assert agency["tool_batch_errors"] >= 1
    assert agency["skill_run_ok"] >= 1
    assert agency["skill_run_error"] >= 1


def test_default_registry_prometheus_includes_probe() -> None:
    default_registry.counter("remedy_prom_probe").inc()
    text = default_registry.prometheus_text()
    assert "remedy_prom_probe" in text


def test_tool_histogram_recorded() -> None:
    """Tool latency histogram lines appear after a timed observe."""
    default_registry.histogram("remedy_tool_duration_seconds", tool="echo").observe(0.01)
    text = default_registry.prometheus_text()
    assert "remedy_tool_duration_seconds_bucket" in text
    assert 'tool="echo"' in text


def test_chat_path_labels_recorded() -> None:
    """Chat path labels used by API routes appear in Prometheus text."""
    default_registry.counter(
        "remedy_chat_requests_total", path="session_stream", status="ok"
    ).inc()
    default_registry.histogram(
        "remedy_chat_duration_seconds", path="session_stream"
    ).observe(0.05)
    default_registry.counter(
        "remedy_chat_requests_total", path="session_message"
    ).inc()
    text = default_registry.prometheus_text()
    assert 'path="session_stream"' in text
    assert 'path="session_message"' in text
    assert "remedy_chat_duration_seconds_bucket" in text


def test_metric_labels_redact_secret_shaped_values() -> None:
    """Label values must never echo API keys into Prometheus / JSON snapshots."""
    secret = "sk-abcdefghijklmnopqrstuvwxyz0123"
    reg = MetricsRegistry()
    reg.counter("remedy_leak_probe_total", tool=secret).inc()
    text = reg.prometheus_text()
    assert secret not in text
    assert "[redacted]" in text
    snap = reg.snapshot()
    dumped = str(snap)
    assert secret not in dumped


def test_health_check_detail_redacts_secrets() -> None:
    hc = HealthChecker()
    hc.register(
        "probe",
        lambda: {"msg": "Authorization: Bearer sk-abcdefghijklmnopqrstuvwxyz0123"},
    )

    async def _run():
        return await hc.check()

    out = asyncio.run(_run())
    blob = str(out)
    assert "sk-abcdefghijklmnopqrstuvwxyz0123" not in blob
    assert out["checks"]["probe"]["status"] == "ok"


