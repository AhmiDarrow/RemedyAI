"""Why did the server stop?  Breadcrumbs for the desktop server panel.

``remedy serve`` runs as a managed sidecar under the desktop.  When it dies the
desktop only sees an OS exit status; the Python logs stop mid-sentence.  This
module makes every way out of the process leave a trace in ``<home>/logs``:

* ``crash.log`` — native tracebacks of all threads on a fatal signal / access
  violation (``faulthandler``), plus any uncaught exception on a thread.
* ``remedy.log`` / ``errors.log`` — which signal asked uvicorn to exit, and a
  CRITICAL line when the server loop ends for any other reason.

Nothing here changes how the server behaves; it only records.
"""

from __future__ import annotations

import contextlib
import faulthandler
import logging
import sys
import threading
from pathlib import Path
from typing import Any

logger = logging.getLogger("remedy.serve")

# uvicorn.main.STARTUP_FAILURE (kept local: the constant is not public API).
_STARTUP_FAILURE = 3

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


def run_uvicorn_logged(app: Any, **kwargs: Any) -> None:
    """``uvicorn.run`` that says why it stopped.

    * an exit signal (SIGINT / SIGTERM / SIGBREAK) is logged with its name
      before uvicorn begins its graceful shutdown;
    * a fatal exception is logged at CRITICAL (and re-raised);
    * a startup failure exits with uvicorn's own STARTUP_FAILURE code.
    """
    import uvicorn

    class _LoggedServer(uvicorn.Server):
        def handle_exit(self, sig: Any, frame: Any) -> None:
            try:
                import signal as _signal

                name = _signal.Signals(sig).name if sig is not None else "?"
            except (ValueError, TypeError):
                name = str(sig)
            logger.critical("serve: exit signal %s received — shutting down the API", name)
            super().handle_exit(sig, frame)

    config = uvicorn.Config(app, **kwargs)
    config.load_app()
    server = _LoggedServer(config)
    try:
        server.run()
    except SystemExit as exc:
        logger.critical("serve: SystemExit(%s) reached the API server loop", exc.code)
        raise
    except KeyboardInterrupt:
        # uvicorn.run swallows this too: Ctrl-C is a normal stop.
        logger.critical("serve: KeyboardInterrupt reached the API server loop")
    except BaseException as exc:  # noqa: BLE001 - last chance to record the cause
        logger.critical("serve: fatal error in the API server loop: %r", exc, exc_info=True)
        raise
    finally:
        logger.warning("serve: API server loop ended (pid %s)", _pid())
    if not server.started and not config.should_reload and config.workers == 1:
        logger.critical("serve: API server never started (bind or startup failure)")
        sys.exit(_STARTUP_FAILURE)


def _pid() -> int:
    import os

    return os.getpid()
