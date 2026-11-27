"""Shared pytest fixtures and env defaults."""

from __future__ import annotations

import os

import pytest

# Default: existing API tests run without Bearer (local unit suite).
# Explicit auth tests set REMEDY_API_AUTH=1 themselves.
os.environ.setdefault("REMEDY_API_AUTH", "0")

# Production: Go remedy-runtime owns messenger inbound + outbound. Python
# network twins were removed; REMEDY_TESTING marks the suite.
os.environ.setdefault("REMEDY_TESTING", "1")

# The suite must never write to — or drive — the owner's real installation.
# Several subsystems (the computer host bridge above all) resolve their home
# from REMEDY_HOME and fall back to ~/.remedy. A test that builds one without
# an explicit tmp home then enqueues jobs into the LIVE queue, which a running
# Desktop app claims and executes: real clicks, real keystrokes, real browser
# navigation on whoever is running the tests. Point the whole suite at a
# throwaway home before any test module is imported.
#
# A test that genuinely needs the real home can still monkeypatch REMEDY_HOME
# back; this is a floor, not a lock.
if not os.environ.get("REMEDY_HOME"):
    import tempfile

    os.environ["REMEDY_HOME"] = tempfile.mkdtemp(prefix="remedy-test-home-")

# Rich colourises when the environment says the terminal can take it, and a
# dozen tests assert on the *text* it prints. With FORCE_COLOR set — which some
# CI images and agent harnesses do — those assertions meet ANSI escapes and
# fail, while the same tests pass on a plain terminal. Pin it off for the suite
# so a test result never depends on what shell it was started from.
os.environ["NO_COLOR"] = "1"
os.environ.pop("FORCE_COLOR", None)
os.environ.setdefault("TERM", "dumb")

# Hidden ``remedy_core`` spawns refuse interpreter/launcher overrides
# (BROWSER, LD_PRELOAD, NODE_OPTIONS, …). Agent and desktop shells often have
# these set; if a test forwards ``os.environ`` into ``run_hidden``, the native
# path returns env_denied. Drop them so the suite does not depend on the
# parent shell. Same idea as NO_COLOR above.
#
# Also drop DISPLAY / WAYLAND_DISPLAY: with a live desktop (or this box's
# agent display) computer observe tools can abort the whole process in glib /
# AT-SPI. The suite must not drive a real display.
for _env_key in (
    "BROWSER",
    "LD_PRELOAD",
    "LD_LIBRARY_PATH",
    "LD_AUDIT",
    "GIT_SSH_COMMAND",
    "GIT_EXEC_PATH",
    "PYTHONSTARTUP",
    "PERL5OPT",
    "NODE_OPTIONS",
    "RUBYOPT",
    "BASH_ENV",
    "ENV",
    "PROMPT_COMMAND",
    "DISPLAY",
    "WAYLAND_DISPLAY",
):
    os.environ.pop(_env_key, None)
for _env_key in list(os.environ):
    if _env_key.startswith("DYLD_"):
        os.environ.pop(_env_key, None)


def _is_missing_core(excinfo) -> bool:
    if excinfo is None:
        return False
    typ = getattr(excinfo, "type", None)
    if getattr(typ, "__name__", "") == "NativeRuntimeUnavailableError":
        return True
    seen: set[int] = set()
    stack = [getattr(excinfo, "value", None)]
    while stack:
        cur = stack.pop()
        if cur is None or id(cur) in seen:
            continue
        seen.add(id(cur))
        if type(cur).__name__ == "NativeRuntimeUnavailableError":
            return True
        msg = str(cur)
        if "remedy_core library not found" in msg or "remedy_core not built" in msg:
            return True
        stack.append(getattr(cur, "__cause__", None))
        stack.append(getattr(cur, "__context__", None))
    return False


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    """Skip tests that need zig remedy_core when the library is not built.

    Tests that already catch NativeRuntimeUnavailableError (fail-closed
    assertions) still pass. This only rewrites an uncaught 'library not
    found' failure into a skip with a clear reason.
    """
    outcome = yield
    if call.when != "call":
        return
    report = outcome.get_result()
    if report.failed and (
        _is_missing_core(call.excinfo) or _failure_mentions_missing_core(report)
    ):
        report.outcome = "skipped"
        report.wasxfail = False
        report.longrepr = "Skipped: remedy_core not built in this environment"


def _failure_mentions_missing_core(report) -> bool:
    parts = [
        str(getattr(report, "longreprtext", "") or ""),
        str(getattr(report, "longrepr", "") or ""),
    ]
    return any("remedy_core library not found" in part for part in parts)


