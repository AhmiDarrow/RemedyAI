"""Shared pytest fixtures and env defaults."""

from __future__ import annotations

import os

import pytest

# Default: existing API tests run without Bearer (local unit suite).
# Explicit auth tests set REMEDY_API_AUTH=1 themselves.
os.environ.setdefault("REMEDY_API_AUTH", "0")

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


@pytest.fixture(autouse=True)
def _reset_provider_breaker():
    """The provider circuit breaker is process-global (by design: one bad
    session must not hammer a dead endpoint). In the suite that state leaked
    from one test's 503s into the next test's retry budget."""
    from remedy.core.providers import clear_provider_quarantine

    clear_provider_quarantine()
    yield
    clear_provider_quarantine()
