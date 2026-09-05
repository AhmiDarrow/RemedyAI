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


@pytest.fixture(autouse=True)
def _reset_provider_breaker():
    """The provider circuit breaker is process-global (by design: one bad
    session must not hammer a dead endpoint). In the suite that state leaked
    from one test's 503s into the next test's retry budget."""
    from remedy.core.providers import clear_provider_quarantine

    clear_provider_quarantine()
    yield
    clear_provider_quarantine()


@pytest.fixture(autouse=True)
def _reset_zig_host_spawn_gates():
    """Write-jail roots and spawn signing state are process-wide in Zig.

    A hop/sandbox test that installs jail roots (or clears the HMAC key) must
    not make later ``run_hidden`` / ``process_exec_capture_authorized`` calls
    fail with ACCESS_DENIED across the rest of the suite.
    """
    import contextlib

    try:
        from remedy.core.computer import host_binding as H
        from remedy.runtime.native_runtime import NativeRuntimeUnavailableError
    except Exception:  # pragma: no cover - import graph mid-collection
        yield
        return

    key = bytes(range(32))
    os.environ.setdefault("REMEDY_SPAWN_SIGNING_KEY", key.hex())

    def _reset() -> None:
        with contextlib.suppress(H.HostError, NativeRuntimeUnavailableError, OSError, AttributeError):
            H.write_jail_clear()
        with contextlib.suppress(H.HostError, NativeRuntimeUnavailableError, OSError, AttributeError):
            H.security_clear_signing_key()
        with contextlib.suppress(H.HostError, NativeRuntimeUnavailableError, OSError, AttributeError, ValueError):
            H.security_set_signing_key(key)
            if hasattr(H, "_set_signing_ready"):
                H._set_signing_ready(True)
            else:
                H._signing_key_ready = True

    _reset()
    yield
    _reset()


@pytest.fixture(autouse=True)
def _reset_live_model_registry():
    """Ids a provider's endpoint listed are remembered process-wide so the
    validator can accept them; one test's mocked listing must not make another
    test's "garbage id" suddenly valid."""
    from remedy.interfaces.model_discovery import forget_live_models

    forget_live_models()
    yield
    forget_live_models()
