"""Structured logging system with rotation, JSON output, and context propagation.

Usage:
    from remedy.core.logging import setup_logging, get_logger
    setup_logging(level="DEBUG", log_dir="~/.remedy/logs")
    log = get_logger(__name__)
    log.info("event", extra={"key": "value"})
"""

from __future__ import annotations

import json
import logging
import os
import sys
from contextlib import suppress
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# -- context propagation ------------------------------------------------------

_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)
_channel: ContextVar[str | None] = ContextVar("channel", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)

# Operator asked for DEBUG (env/config). Distinct from the always-on debug.log
# ring under setup_serve_logging — hot-path traces only when this is True.
_hot_debug: bool = False


def hot_debug_enabled() -> bool:
    """True when operator set DEBUG (REMEDY_LOG_LEVEL / config), not mere file ring."""
    return _hot_debug


def set_log_context(
    session_id: str | None = None,
    channel: str | None = None,
    request_id: str | None = None,
) -> None:
    if session_id is not None:
        _session_id.set(session_id)
    if channel is not None:
        _channel.set(channel)
    if request_id is not None:
        _request_id.set(request_id)


def clear_log_context() -> None:
    _session_id.set(None)
    _channel.set(None)
    _request_id.set(None)


# -- structured formatter -----------------------------------------------------


def _redact_log_text(text: str) -> str:
    """Best-effort secret scrub for log lines (fail soft — never raise)."""
    if not text:
        return ""
    try:
        from remedy.core.metabolism.redact import redact_text

        return redact_text(text)
    except Exception:
        return text


def _redact_log_obj(obj: Any, *, depth: int = 0) -> Any:
    if depth > 6:
        return "[redacted-depth]"
    try:
        from remedy.core.metabolism.redact import redact_obj

        return redact_obj(obj, depth=depth)
    except Exception:
        if isinstance(obj, str):
            return _redact_log_text(obj)
        return obj


class StructuredFormatter(logging.Formatter):
    """JSON formatter for structured logging with context propagation."""

    def __init__(self, fmt: str | None = None, color: bool = True) -> None:
        super().__init__()
        self.color = bool(color and sys.stderr is not None and sys.stderr.isatty())

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).isoformat()

        if self.color and record.levelno >= logging.WARNING:
            return self._format_colored(record, ts)
        else:
            return self._format_json(record, ts)

    def _format_json(self, record: logging.LogRecord, ts: str) -> str:
        data: dict[str, Any] = {
            "ts": ts,
            "level": record.levelname,
            "logger": record.name,
            "msg": _redact_log_text(record.getMessage()),
        }

        sid = _session_id.get()
        if sid:
            data["session"] = sid
        ch = _channel.get()
        if ch:
            data["channel"] = ch
        rid = _request_id.get()
        if rid:
            data["request_id"] = rid

        if record.exc_info and record.exc_info[1]:
            data["error"] = _redact_log_text(str(record.exc_info[1]))
            data["error_type"] = type(record.exc_info[1]).__name__

        if hasattr(record, "extra") and record.extra:
            scrubbed = _redact_log_obj(record.extra)
            if isinstance(scrubbed, dict):
                data.update(scrubbed)

        return json.dumps(data, default=str)

    def _format_colored(self, record: logging.LogRecord, ts: str) -> str:
        colors = {
            logging.WARNING: "\033[33m",
            logging.ERROR: "\033[31m",
            logging.CRITICAL: "\033[35m",
        }
        reset = "\033[0m"
        color = colors.get(record.levelno, "")
        msg = _redact_log_text(record.getMessage())
        base = f"{ts} [{color}{record.levelname}{reset}] {record.name}: {msg}"
        if record.exc_info and record.exc_info[1]:
            base += f" | {color}{_redact_log_text(str(record.exc_info[1]))}{reset}"
        return base


class TextFormatter(logging.Formatter):
    """Human-readable formatter with context."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        sid = _session_id.get()
        extra = f" [{sid[:8]}]" if sid else ""
        msg = _redact_log_text(record.getMessage())
        return f"{ts} {record.levelname:5s}{extra} {record.name}: {msg}"


# -- setup --------------------------------------------------------------------


def setup_logging(
    level: str = "INFO",
    log_dir: str | None = None,
    json_output: bool = True,
    console_output: bool = True,
) -> None:
    """Configure root logger with structured output, optional file rotation, and context propagation."""
    from logging.handlers import RotatingFileHandler

    global _hot_debug
    lvl_name = (level or "INFO").upper()
    _hot_debug = lvl_name == "DEBUG"

    root = logging.getLogger()
    root.setLevel(getattr(logging, lvl_name, logging.INFO))
    root.handlers.clear()

    if json_output and console_output:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(StructuredFormatter())
        root.addHandler(handler)
    elif console_output:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(TextFormatter())
        root.addHandler(handler)

    if log_dir:
        p = Path(log_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        # Rotate so long desktop sessions don't grow unbounded.
        fh = RotatingFileHandler(
            p / "remedy.log",
            maxBytes=5 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(StructuredFormatter(color=False))
        root.addHandler(fh)

        # Error-only log (smaller, easy to skim for failures)
        eh = RotatingFileHandler(
            p / "errors.log",
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        eh.setLevel(logging.ERROR)
        eh.setFormatter(StructuredFormatter(color=False))
        root.addHandler(eh)

        # Debug ring — always captures DEBUG+ even when console is INFO.
        # Enable via REMEDY_LOG_LEVEL=DEBUG or config log_level=DEBUG for console;
        # this file is always DEBUG so perf issues leave a trail without spam.
        dh = RotatingFileHandler(
            p / "debug.log",
            maxBytes=8 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        dh.setLevel(logging.DEBUG)
        dh.setFormatter(StructuredFormatter(color=False))
        root.addHandler(dh)
        # Ensure DEBUG records are emitted to the debug file even if root is INFO.
        root.setLevel(min(root.level, logging.DEBUG))
        # Console/file handlers keep their own levels; only dh is DEBUG.
        for h in root.handlers:
            if h is not dh and h.level == logging.NOTSET:
                h.setLevel(getattr(logging, lvl_name, logging.INFO))

    # Shush noisy libraries
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "aiohttp", "multipart"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def resolve_log_level(config: dict | None = None) -> str:
    """Pick log level from env then config (default INFO)."""
    env = (os.environ.get("REMEDY_LOG_LEVEL") or "").strip().upper()
    if env in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
        return env
    if isinstance(config, dict):
        raw = config.get("log_level")
        if isinstance(config.get("log"), dict) and not raw:
            raw = config["log"].get("level")
        lvl = str(raw or "INFO").strip().upper()
        if lvl in ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"):
            return lvl
    return "INFO"


def setup_serve_logging(
    home_dir: str | Path,
    *,
    level: str | None = None,
    config: dict | None = None,
) -> Path:
    """Configure logging for ``remedy serve`` / desktop sidecar.

    Always writes rotating files under ``{home}/logs/``:
      - remedy.log  (level from config/env)
      - errors.log  (ERROR+)
      - debug.log   (DEBUG+, for diagnosing sluggish UI / disconnects)

    Returns the log directory path.
    """
    home = Path(home_dir).expanduser().resolve()
    log_dir = home / "logs"
    lvl = level or resolve_log_level(config)
    # Desktop sidecar: prefer human-readable console lines (Tauri captures them).
    # File handlers stay JSON for tooling.
    setup_logging(
        level=lvl,
        log_dir=str(log_dir),
        json_output=False,
        console_output=True,
    )
    log = get_logger("remedy.serve")
    log.info(
        "Logging initialized level=%s dir=%s (remedy.log, errors.log, debug.log)",
        lvl,
        log_dir,
    )
    # Stale stream locks from a killed sidecar must not block self-inject forever.
    with suppress(Exception):
        from remedy.core.stream_lock import clear_stale_stream_locks

        clear_stale_stream_locks(home)
    return log_dir


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
