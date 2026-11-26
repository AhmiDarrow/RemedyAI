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


@pytest.fixture(autouse=True)
def _reset_search_cooldown():
    """A DuckDuckGo human check pauses search for ten minutes, process-wide.
    One test that trips it must not make every later search test read
    "search is blocked"."""
    from remedy.core.web_helpers import _ddg_reset_block

    _ddg_reset_block()
    yield
    _ddg_reset_block()


def core_available() -> bool:
    """True when the zig remedy_core library is loadable in this environment."""
    try:
        from remedy.core.computer import host_binding

        host_binding._lib()
        return True
    except Exception:
        return False


def public_dns_available() -> bool:
    """True when a well-known public host resolves to a public IP."""
    try:
        from remedy.core.web_helpers import _resolve_public_ips

        return bool(_resolve_public_ips("example.com"))
    except OSError:
        return False


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


@pytest.fixture
def stub_public_dns(monkeypatch: pytest.MonkeyPatch) -> None:
    """Stub public-IP resolution so URL tests can run without live DNS."""

    def fake_resolve(hostname: str) -> list[str]:
        host = (hostname or "").strip().lower().rstrip(".")
        if not host or host.endswith(".invalid") or " " in host:
            return []
        return ["8.8.8.8"]

    monkeypatch.setattr("remedy.core.web_helpers._resolve_public_ips", fake_resolve)
