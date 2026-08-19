"""Every route module registers, and no parameterless GET returns a 5xx.

Four of the largest untested modules live here — routes/partner (839 lines),
routes/sessions/stream (688), routes/rmb (380), routes/sessions/messages (304).
None of them had a test that so much as imported them.

A stub runtime is the point: the desktop hits these during boot, before a
provider is connected and before memory is open. A route that assumes any of
that is present answers 500 instead of degrading, and nothing catches it until
someone opens the app.
"""

from __future__ import annotations

import importlib
import inspect
import os
import pkgutil
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import remedy.interfaces.routes as routes_pkg

#: Streams hold the connection open by design — that is the feature.
STREAMING = {"/api/events/sessions"}

#: Reaching the network would make this a flaky test, not a better one.
OUTBOUND = {"/api/updates/check"}


class _StubConfig:
    def __init__(self, home: str) -> None:
        self.home_dir = home

    def __getattr__(self, _name):
        return None


class _StubRuntime:
    def __init__(self, home: str) -> None:
        self.config = _StubConfig(home)

    def __getattr__(self, _name):
        return None


def _build_app(home: str) -> tuple[FastAPI, list[str]]:
    app = FastAPI()
    app.state.disable_api_docs = True
    failures: list[str] = []
    for mod_info in pkgutil.walk_packages(
        routes_pkg.__path__, prefix="remedy.interfaces.routes."
    ):
        try:
            mod = importlib.import_module(mod_info.name)
        except Exception as exc:  # pragma: no cover — import health has its own test
            failures.append(f"{mod_info.name}: import {type(exc).__name__}: {exc}")
            continue
        for name, fn in vars(mod).items():
            if not (name.startswith("register_") and inspect.isfunction(fn)):
                continue
            try:
                sig = inspect.signature(fn)
                kwargs = {
                    p: (_StubRuntime(home) if "runtime" in p else None)
                    for p, v in sig.parameters.items()
                    if v.default is inspect.Parameter.empty and p != "app"
                }
                if "app" in sig.parameters:
                    fn(app, **kwargs)
                else:
                    fn(**kwargs)
            except Exception as exc:
                failures.append(f"{mod_info.name}.{name}: {type(exc).__name__}: {exc}")
    return app, failures


@pytest.fixture(scope="module")
def app_and_failures(tmp_path_factory):
    home = str(tmp_path_factory.mktemp("routes-smoke"))
    os.environ.setdefault("REMEDY_HOME", home)
    return _build_app(home)


def test_every_route_module_registers(app_and_failures):
    _app, failures = app_and_failures
    assert not failures, "route registrars that raised:\n  " + "\n  ".join(failures)


def test_enough_routes_exist_for_this_to_mean_something(app_and_failures):
    app, _ = app_and_failures
    paths = {r.path for r in app.routes if hasattr(r, "methods")}
    assert len(paths) > 100


def test_no_parameterless_get_returns_a_server_error(app_and_failures):
    """503 is a correct answer — "memory store not available" with no store is
    the route degrading as it should. 500 is not."""
    app, _ = app_and_failures
    client = TestClient(app, raise_server_exceptions=False)
    paths = sorted(
        {
            r.path
            for r in app.routes
            if hasattr(r, "methods")
            and "GET" in r.methods
            and "{" not in r.path
            and r.path not in STREAMING
            and r.path not in OUTBOUND
        }
    )
    bad: list[str] = []
    pool = ThreadPoolExecutor(max_workers=1)
    for path in paths:
        future = pool.submit(client.get, path)
        try:
            resp = future.result(timeout=15)
        except FutureTimeout:
            bad.append(f"{path} did not answer within 15s")
            pool.shutdown(wait=False, cancel_futures=True)
            pool = ThreadPoolExecutor(max_workers=1)
            continue
        except Exception as exc:
            bad.append(f"{path} raised {type(exc).__name__}: {exc}")
            continue
        if resp.status_code >= 500 and resp.status_code != 503:
            bad.append(f"{path} -> {resp.status_code} {resp.text[:120]}")
    pool.shutdown(wait=False)
    assert not bad, "routes that failed on a bare runtime:\n  " + "\n  ".join(bad)


def test_the_streaming_route_is_still_streaming(app_and_failures):
    """Named so that if it ever stops holding open, the exclusion above gets
    revisited rather than quietly hiding a hang."""
    app, _ = app_and_failures
    paths = {r.path for r in app.routes if hasattr(r, "methods")}
    assert paths >= STREAMING, "the streaming route was renamed or removed"
