"""Optional crash breadcrumbs for long-lived Python workers.

Production ``:7400`` is owned by Go ``remedy-runtime`` — Python no longer
runs uvicorn for the local API. ``run_uvicorn_logged`` was removed with that
cutover. This module only keeps ``faulthandler`` helpers for workers that
still want a crash.log under ``<home>/logs``.
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("remedy.serve")

_crash_file: Any = None


def crash_log_path(home: str | Path) -> Path:
    return Path(home).expanduser() / "logs" / "crash.log"


def enable_crash_forensics(home: str | Path) -> Path | None:
    """Point ``faulthandler`` at ``crash.log`` and log uncaught thread errors.

    Returns the crash log path, or ``None`` when the logs folder is unusable.
    Safe to call more than once; the first successful call wins.
    """
    global _crash_file
    if _crash_file is not None:
        return crash_log_path(home)
    path = crash_log_path(home)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        # Keep the file bounded: a crash log that grows forever is unreadable.
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.write_text("")
        handle = open(path, "a", encoding="utf-8")  # noqa: SIM115 - must outlive this call
    except OSError:
        return None
    _crash_file = handle
    try:
        faulthandler.enable(file=handle, all_threads=True)
    except (OSError, RuntimeError, ValueError):
        return None

    prior_hook = threading.excepthook

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        # SystemExit on a worker thread is a deliberate stop, not a crash.
        if args.exc_type is SystemExit:
            return
        name = getattr(args.thread, "name", "?")
        exc = args.exc_value if args.exc_value is not None else args.exc_type()
        logger.error(
            "uncaught exception on thread %s: %s",
            name,
            exc,
            exc_info=(args.exc_type, exc, args.exc_traceback),
        )
        with contextlib.suppress(Exception):
            prior_hook(args)

    threading.excepthook = _thread_hook
    return path
