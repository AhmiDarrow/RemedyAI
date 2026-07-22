"""Phase 7 tests: Polish & Production Readiness."""

import asyncio
import logging

import pytest

from remedy.core.errors import (
    APIRetryPolicy,
    ConfigError,
    ExecutionError,
    GatewayError,
    MemoryError,
    RemedyError,
    SecurityError,
    SkillError,
)
from remedy.core.logging import (
    StructuredFormatter,
    TextFormatter,
    _channel,
    _request_id,
    _session_id,
    clear_log_context,
    set_log_context,
    setup_logging,
)
from remedy.core.metrics import (
    Counter,
    Gauge,
    HealthChecker,
    Histogram,
    MetricsRegistry,
)
from remedy.core.security import (
    safe_path,
    sanitize_search_query,
    validate_execution_command,
    validate_memory_entry_content,
    validate_skill_name,
    validate_tags,
    validate_uuid,
)

# ============================================================================
# Test Error Types
# ============================================================================

class TestErrorTypes:
    def test_remedy_error_base(self):
        e = RemedyError("test error")
        assert str(e) == "test error"
        assert e.code == "INTERNAL_ERROR"
        assert isinstance(e.details, dict)
        assert e.timestamp > 0

    def test_remedy_error_with_code(self):
        e = RemedyError("msg", code="CUSTOM")
        assert e.code == "CUSTOM"

    def test_config_error(self):
        e = ConfigError("bad config", key="log_level")
        assert e.code == "CONFIG_ERROR"
        assert e.details == {"key": "log_level"}

    def test_skill_error(self):
        e = SkillError("load failed", skill_name="test_skill", line=42)
        assert e.code == "SKILL_ERROR"
        assert e.details["skill_name"] == "test_skill"
        assert e.details["line"] == 42

    def test_memory_error(self):
        e = MemoryError("db locked")
        assert e.code == "MEMORY_ERROR"

    def test_gateway_error(self):
        e = GatewayError("timeout", channel="telegram")
        assert e.code == "GATEWAY_ERROR"
        assert e.details["channel"] == "telegram"

    def test_execution_error(self):
        e = ExecutionError("segfault", tool_name="bash")
        assert e.code == "EXECUTION_ERROR"
        assert e.details["tool_name"] == "bash"

    def test_security_error(self):
        e = SecurityError("path escape", rule="path_traversal")
        assert e.code == "SECURITY_ERROR"
        assert e.details["rule"] == "path_traversal"

    def test_error_hierarchy(self):
        assert issubclass(ConfigError, RemedyError)
        assert issubclass(SkillError, RemedyError)
        assert issubclass(MemoryError, RemedyError)
        assert issubclass(GatewayError, RemedyError)
        assert issubclass(ExecutionError, RemedyError)
        assert issubclass(SecurityError, RemedyError)


# ============================================================================
# Test Logging
# ============================================================================

class TestStructuredFormatter:
    def test_json_output(self, caplog):
        formatter = StructuredFormatter(color=False)
        logger = logging.getLogger("test.structured")
        logger.setLevel(logging.DEBUG)
        handler = logging.StreamHandler(open("NUL", "w")) if hasattr(open, '__name__') else None  # noqa
        record = logging.LogRecord(
            "test.structured", logging.INFO, "", 0, "hello world", (), None
        )
        output = formatter.format(record)
        assert "test.structured" in output
        assert "hello world" in output
        assert "INFO" in output
        assert '"ts"' in output

    def test_json_with_context(self):
        set_log_context(session_id="sess-001", channel="web", request_id="req-42")
        formatter = StructuredFormatter(color=False)
        record = logging.LogRecord("test.ctx", logging.INFO, "", 0, "contextual", (), None)
        output = formatter.format(record)
        assert "sess-001" in output
        assert "web" in output
        assert "req-42" in output
        clear_log_context()

    def test_json_with_error(self):
        formatter = StructuredFormatter(color=False)
        try:
            raise ValueError("boom")
        except ValueError:
            record = logging.LogRecord(
                "test.err", logging.ERROR, "", 0, "error msg", (), None
            )
            import sys
            record.exc_info = sys.exc_info()
            output = formatter.format(record)
            assert "boom" in output
            assert "ValueError" in output

    def test_text_formatter(self):
        formatter = TextFormatter()
        record = logging.LogRecord("test.text", logging.INFO, "", 0, "simple", (), None)
        output = formatter.format(record)
        assert "INFO" in output
        assert "simple" in output


class TestLoggingSetup:
    def test_setup_logging_json(self):
        setup_logging(level="DEBUG", json_output=True, console_output=True)
        root = logging.getLogger()
        handlers = root.handlers
        assert len(handlers) >= 1
        assert isinstance(handlers[0].formatter, StructuredFormatter)

    def test_setup_logging_text(self):
        setup_logging(level="DEBUG", json_output=False, console_output=True)
        root = logging.getLogger()
        handlers = root.handlers
        assert len(handlers) >= 1
        assert isinstance(handlers[0].formatter, TextFormatter)

    def test_setup_logging_with_file(self, tmp_path):
        log_dir = tmp_path / "logs"
        setup_logging(level="INFO", log_dir=str(log_dir), json_output=False, console_output=False)
        assert (log_dir / "remedy.log").exists()
        assert (log_dir / "errors.log").exists()

    def test_clear_log_context(self):
        set_log_context(session_id="x", channel="y", request_id="z")
        clear_log_context()
        assert _session_id.get() is None
        assert _channel.get() is None
        assert _request_id.get() is None


# ============================================================================
# Test Metrics
# ============================================================================

class TestCounter:
    def test_inc_default(self):
        c = Counter(name="requests")
        assert c.inc() == 1
        assert c.inc(3) == 4
        assert c.value == 4

    def test_snapshot(self):
        c = Counter(name="errors", labels={"type": "timeout"})
        c.inc(5)
        snap = c.snapshot()
        assert snap["name"] == "errors"
        assert snap["value"] == 5
        assert snap["labels"]["type"] == "timeout"


class TestGauge:
    def test_set(self):
        g = Gauge(name="memory_mb")
        g.set(256.5)
        assert g.value == 256.5
        g.set(512.0)
        assert g.value == 512.0

    def test_snapshot(self):
        g = Gauge(name="latency", labels={"percentile": "p99"})
        g.set(0.125)
        snap = g.snapshot()
        assert snap["name"] == "latency"
        assert snap["value"] == 0.125


class TestHistogram:
    def test_observe(self):
        h = Histogram(name="duration", buckets=[1.0, 5.0, 10.0])
        h.observe(0.5)
        h.observe(3.0)
        h.observe(12.0)
        assert h._counts[0] == 1  # <= 1.0
        assert h._counts[1] == 1  # <= 5.0
        assert h._counts[3] == 1  # Inf bucket

    def test_snapshot(self):
        h = Histogram(name="latency_ms")
        h.observe(2.0)
        snap = h.snapshot()
        assert snap["name"] == "latency_ms"
        assert snap["count"] == 1


class TestMetricsRegistry:
    def test_counter_get_or_create(self):
        reg = MetricsRegistry()
        c1 = reg.counter("hits")
        c2 = reg.counter("hits")
        assert c1 is c2
        c1.inc()
        assert c2.value == 1

    def test_counter_with_labels(self):
        reg = MetricsRegistry()
        reg.counter("errors", source="api").inc(3)
        reg.counter("errors", source="web").inc(5)
        snap = reg.snapshot()
        assert len(snap["counters"]) == 2

    def test_gauge(self):
        reg = MetricsRegistry()
        reg.gauge("temp").set(37.0)
        assert reg.num_gauges == 1

    def test_histogram(self):
        reg = MetricsRegistry()
        reg.histogram("response_time").observe(0.25)
        assert reg.num_histograms == 1

    def test_snapshot_aggregate(self):
        reg = MetricsRegistry()
        reg.counter("total").inc(10)
        reg.gauge("mem").set(100.0)
        reg.histogram("dur").observe(0.5)
        snap = reg.snapshot()
        assert len(snap["counters"]) == 1
        assert len(snap["gauges"]) == 1
        assert len(snap["histograms"]) == 1

    def test_describe(self):
        reg = MetricsRegistry()
        reg.counter("a").inc(1)
        reg.gauge("b").set(2.0)
        lines = reg.describe()
        assert len(lines) == 2


class TestHealthChecker:
    def test_all_healthy(self):
        hc = HealthChecker()
        hc.register("db", lambda: "connected")

        async def check():
            return await hc.check()

        result = asyncio.run(check())
        assert result["status"] == "ok"
        assert result["checks"]["db"]["status"] == "ok"

    def test_unhealthy_component(self):
        hc = HealthChecker()

        def failing():
            raise RuntimeError("connection refused")

        hc.register("db", failing)

        async def check():
            return await hc.check()

        result = asyncio.run(check())
        assert result["status"] == "degraded"
        assert result["checks"]["db"]["status"] == "unhealthy"

    def test_unregister(self):
        hc = HealthChecker()
        hc.register("a", lambda: "ok")
        hc.register("b", lambda: "ok")
        hc.unregister("a")
        assert "a" not in hc._checks

    def test_uptime_tracks(self):
        import time
        hc = HealthChecker()
        time.sleep(0.1)

        async def check():
            return await hc.check()

        result = asyncio.run(check())
        assert result["uptime_seconds"] >= 0.1


# ============================================================================
# Test Security
# ============================================================================

class TestSafePath:
    def test_valid_subpath(self, tmp_path):
        base = tmp_path / "sandbox"
        base.mkdir()
        result = safe_path("subdir/file.txt", base_dir=base)
        assert result == (base / "subdir/file.txt").resolve()

    def test_path_traversal_rejected(self, tmp_path):
        base = tmp_path / "sandbox"
        base.mkdir()
        with pytest.raises(SecurityError, match="Path traversal"):
            safe_path("../../etc/passwd", base_dir=base)

    def test_too_deep_path(self, tmp_path):
        base = tmp_path / "shallow"
        base.mkdir()
        deep = "/".join([str(i) for i in range(100)])
        with pytest.raises(SecurityError):
            safe_path(deep, base_dir=base)

    def test_invalid_chars(self, tmp_path):
        base = tmp_path / "box"
        base.mkdir()
        with pytest.raises(SecurityError):
            safe_path("file<script>.txt", base_dir=base)

    def test_home_dir_default(self):
        result = safe_path("session_notes/test.md")
        assert result.parent.name == "session_notes"


class TestSkillNameValidation:
    def test_valid_name(self):
        assert validate_skill_name("memory-backup") == "memory-backup"

    def test_strips_and_lowers(self):
        assert validate_skill_name("  BACKUP-Tool  ") == "backup-tool"

    def test_empty_rejected(self):
        with pytest.raises(SecurityError):
            validate_skill_name("   ")

    def test_invalid_chars(self):
        with pytest.raises(SecurityError):
            validate_skill_name("test; DROP TABLE users")


class TestTagValidation:
    def test_valid_tags(self):
        result = validate_tags(["utility", "backup", "memory"])
        assert result == ["utility", "backup", "memory"]

    def test_deduplicates(self):
        result = validate_tags(["tag", "TAG", " tag "])
        assert result == ["tag"]

    def test_max_20_tags(self):
        tags = [f"t{i}" for i in range(30)]
        result = validate_tags(tags)
        assert len(result) <= 20

    def test_invalid_tag(self):
        with pytest.raises(SecurityError):
            validate_tags(["good", "bad<script>"])


class TestUUIDValidation:
    def test_valid_uuid(self):
        uid = "550e8400-e29b-41d4-a716-446655440000"
        assert validate_uuid(uid) == uid

    def test_invalid_uuid(self):
        with pytest.raises(SecurityError):
            validate_uuid("not-a-uuid", context="character_id")


class TestSanitizeSearchQuery:
    def test_valid_query(self):
        assert sanitize_search_query("hello world") == "hello world"

    def test_removes_special_chars(self):
        result = sanitize_search_query('SELECT * FROM users"*')
        assert '"' not in result
        assert "*" not in result

    def test_empty_rejected(self):
        with pytest.raises(SecurityError):
            sanitize_search_query("")

    def test_too_long(self):
        with pytest.raises(SecurityError):
            sanitize_search_query("x" * 2000)

    def test_only_special(self):
        with pytest.raises(SecurityError):
            sanitize_search_query('*"*"')


class TestMemoryContentValidation:
    def test_valid_content(self):
        assert validate_memory_entry_content("normal content") == "normal content"

    def test_too_long(self):
        with pytest.raises(SecurityError):
            validate_memory_entry_content("x" * 200_000)


class TestExecutionCommandValidation:
    def test_valid_command(self):
        cmd = ["python", "--version"]
        assert validate_execution_command(cmd) == cmd

    def test_empty_list(self):
        with pytest.raises(SecurityError):
            validate_execution_command([])

    def test_non_string_args(self):
        with pytest.raises(SecurityError):
            validate_execution_command(["echo", 42])


# ============================================================================
# Test Retry Policy
# ============================================================================

class TestRetryPolicy:
    def test_connection_error_retries(self):
        policy = APIRetryPolicy(
            name="api", condition="connection_error", max_retries=2, base_delay=0.01
        )
        assert policy.should_retry(ConnectionError("refused")) is True
        assert policy.should_retry(TimeoutError("timed out")) is True

    def test_rate_limit_retries(self):
        policy = APIRetryPolicy(
            name="api", condition="rate_limit", max_retries=2, base_delay=0.01
        )
        assert policy.should_retry(Exception("429 Too Many Requests")) is True
        assert policy.should_retry(Exception("503 service unavailable")) is True

    def test_non_retryable_error(self):
        policy = APIRetryPolicy(
            name="api", condition="connection_error", max_retries=2, base_delay=0.01
        )
        assert policy.should_retry(ValueError("invalid")) is False

    def test_delay_exponential_backoff(self):
        policy = APIRetryPolicy(
            name="test",
            condition="all",
            max_retries=3,
            base_delay=0.1,
            backoff_multiplier=2.0,
            jitter=False,
        )
        d0 = policy.delay_for_attempt(0)
        d1 = policy.delay_for_attempt(1)
        d2 = policy.delay_for_attempt(2)
        assert d0 == 0.1
        assert d1 == 0.2
        assert d2 == 0.4

    def test_delay_capped_by_max(self):
        policy = APIRetryPolicy(
            name="test",
            condition="all",
            max_retries=5,
            base_delay=1.0,
            max_delay=5.0,
            jitter=False,
        )
        d = policy.delay_for_attempt(10)
        assert d <= 5.0

    def test_execute_success(self):
        policy = APIRetryPolicy(name="x", condition="all", max_retries=2, base_delay=0.01)

        async def ok():
            return "done"

        result = asyncio.run(policy.execute(ok))
        assert result == "done"

    def test_execute_retries_then_succeeds(self):
        policy = APIRetryPolicy(
            name="x", condition="all", max_retries=3, base_delay=0.01
        )
        calls = []

        async def flaky():
            calls.append(1)
            if len(calls) < 3:
                raise ConnectionError("not yet")
            return "finally"

        result = asyncio.run(policy.execute(flaky))
        assert result == "finally"
        assert len(calls) == 3

    def test_execute_exhausts_retries(self):
        policy = APIRetryPolicy(
            name="x", condition="all", max_retries=2, base_delay=0.01
        )

        async def always_fails():
            raise ConnectionError("nope")

        with pytest.raises(ConnectionError):
            asyncio.run(policy.execute(always_fails))
