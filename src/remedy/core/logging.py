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
import sys
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

# -- context propagation ------------------------------------------------------

_session_id: ContextVar[str | None] = ContextVar("session_id", default=None)
_channel: ContextVar[str | None] = ContextVar("channel", default=None)
_request_id: ContextVar[str | None] = ContextVar("request_id", default=None)


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
            "msg": record.getMessage(),
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
            data["error"] = str(record.exc_info[1])
            data["error_type"] = type(record.exc_info[1]).__name__

        if hasattr(record, "extra") and record.extra:
            data.update(record.extra)

        return json.dumps(data, default=str)

    def _format_colored(self, record: logging.LogRecord, ts: str) -> str:
        colors = {
            logging.WARNING: "\033[33m",
            logging.ERROR: "\033[31m",
            logging.CRITICAL: "\033[35m",
        }
        reset = "\033[0m"
        color = colors.get(record.levelno, "")
        base = f"{ts} [{color}{record.levelname}{reset}] {record.name}: {record.getMessage()}"
        if record.exc_info and record.exc_info[1]:
            base += f" | {color}{record.exc_info[1]}{reset}"
        return base


class TextFormatter(logging.Formatter):
    """Human-readable formatter with context."""

    def format(self, record: logging.LogRecord) -> str:
        ts = datetime.now(UTC).strftime("%H:%M:%S")
        sid = _session_id.get()
        extra = f" [{sid[:8]}]" if sid else ""
        return f"{ts} {record.levelname:5s}{extra} {record.name}: {record.getMessage()}"


# -- setup --------------------------------------------------------------------


def setup_logging(
    level: str = "INFO",
    log_dir: str | None = None,
    json_output: bool = True,
    console_output: bool = True,
) -> None:
    """Configure root logger with structured output, optional file rotation, and context propagation."""
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
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
        fh = logging.FileHandler(
            p / "remedy.log",
            encoding="utf-8",
        )
        fh.setFormatter(StructuredFormatter(color=False))
        root.addHandler(fh)

        # Error-only log
        eh = logging.FileHandler(
            p / "errors.log",
            encoding="utf-8",
        )
        eh.setLevel(logging.ERROR)
        eh.setFormatter(StructuredFormatter(color=False))
        root.addHandler(eh)

    # Shush noisy libraries
    for noisy in ("httpx", "httpcore", "urllib3", "asyncio", "aiohttp"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
