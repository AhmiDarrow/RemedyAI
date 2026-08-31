"""Regression: frozen windowed sidecar must not crash on None stdout/stderr.

The PyInstaller --noconsole sidecar starts Python with ``sys.stdout`` and
``sys.stderr`` both None. uvicorn's ColourizedFormatter calls
``sys.stdout.isatty()`` at config time, which raised
``AttributeError: 'NoneType' object has no attribute 'isatty'`` and aborted
the server before it bound a port. ``_ensure_stdio()`` replaces the None
streams with a null stream so formatter config and StreamHandler writes
degrade gracefully.
"""

from __future__ import annotations

import sys


def test_ensure_stdio_replaces_none_streams():
    from remedy.interfaces.cli.cmd_runtime import _ensure_stdio

    saved_stdout, saved_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None
        _ensure_stdio()
        assert sys.stdout is not None
        assert sys.stderr is not None
        # The replacements must behave like a closed non-tty stream.
        assert sys.stdout.isatty() is False
        sys.stdout.write("swallowed")
        sys.stdout.flush()
        try:
            sys.stdout.fileno()
            raised = False
        except OSError:
            raised = True
        assert raised, "null stream fileno() must raise OSError like no console"
    finally:
        sys.stdout, sys.stderr = saved_stdout, saved_stderr


def test_uvicorn_formatter_survives_null_stdout():
    """The exact crash: use_colors=None -> isatty() on the None stream."""
    import logging.config

    from remedy.interfaces.cli.cmd_runtime import _ensure_stdio

    saved_stdout, saved_stderr = sys.stdout, sys.stderr
    try:
        sys.stdout = None
        sys.stderr = None
        _ensure_stdio()
        logging.config.dictConfig(
            {
                "version": 1,
                "disable_existing_loggers": False,
                "formatters": {
                    "default": {
                        "()": "uvicorn.logging.DefaultFormatter",
                        "fmt": "%(levelprefix)s %(message)s",
                        "use_colors": None,
                    }
                },
                "handlers": {
                    "default": {
                        "class": "logging.StreamHandler",
                        "formatter": "default",
                        "stream": "ext://sys.stdout",
                    }
                },
                "root": {"handlers": ["default"], "level": "INFO"},
            }
        )
        logger = logging.getLogger("uvicorn.test")
        logger.info("no console attached")  # must not raise
    finally:
        sys.stdout, sys.stderr = saved_stdout, saved_stderr
