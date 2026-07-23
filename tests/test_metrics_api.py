"""Metrics registry + HTTP /api/metrics tests."""

from __future__ import annotations

from fastapi.testclient import TestClient

from remedy.core.metrics import MetricsRegistry, default_registry
from remedy.interfaces.api import create_app


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


def test_api_metrics_json() -> None:
    default_registry.counter("remedy_test_counter").inc()
    client = TestClient(create_app())
    r = client.get("/api/metrics")
    assert r.status_code == 200
    data = r.json()
    assert "metrics" in data
    assert "health" in data
    assert data["health"]["status"] in ("ok", "degraded")


def test_api_metrics_prometheus() -> None:
    default_registry.counter("remedy_prom_probe").inc()
    client = TestClient(create_app())
    r = client.get("/api/metrics", params={"format": "prometheus"})
    assert r.status_code == 200
    assert "text/plain" in r.headers.get("content-type", "")
    assert "remedy_prom_probe" in r.text


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
