"""Call every registered agent tool with its defaults; none may raise.

This is the technique that found `/security-status`: run it, do not read it.
A tool that raises on a bare call is a dead turn — the model asks for it, gets a
traceback, and the owner sees an error where an answer should be. Static
analysis cannot see it, because the code is valid; only the branch is wrong.

147 tools across every `register_*_tools` factory in `remedy.core`.
"""

from __future__ import annotations

import asyncio
import contextlib
import importlib
import inspect
import os
import pkgutil
from typing import Any

import pytest

import remedy.core as core_pkg

#: Errors that mean the tool itself is broken, rather than the environment
#: being absent. A missing provider is fine; a missing attribute is not.
PROGRAMMING_ERRORS = (
    NameError,
    UnboundLocalError,
    AttributeError,
    KeyError,
    IndexError,
)

#: NEVER driven. These reach past the process into the machine or the network:
#: they enqueue jobs a running Desktop claims and executes against the owner's
#: live browser rail, move the mouse, send keystrokes, read and overwrite the
#: real clipboard, or start a background thread that calls a model server.
#: A test that calls them hijacks the desktop of whoever runs the suite — and
#: one of them (clipboard_read) used to bring the whole process down with a
#: Windows access violation, which no ``except`` can catch.
#:
#: Redirecting REMEDY_HOME is not enough on its own: any path that resolves the
#: host bridge some other way lands back on the real queue, and the clipboard
#: has no notion of a test home at all. So they are excluded by construction.
NEVER_DRIVE_MODULES = {
    "remedy.core.agent_computer_tools",
    "remedy.core.agent_companion_tools",
}
NEVER_DRIVE_PREFIXES = (
    "computer_",
    "desktop_",
    "browser_",
    "clipboard_",
    "companion_",
)
#: Individually dangerous tools from otherwise safe modules.
NEVER_DRIVE_NAMES = {
    "soul_dream",  # spawns a thread that HTTP-calls the local model server
}


class _Task:
    def __init__(self, title: str, description: str = "", tags: list | None = None):
        self.title = title
        self.description = description
        self.tags = list(tags or [])
        self.status = "open"
        self.id = f"task-{abs(hash(title)) % 10000}"


class _Registry:
    def __init__(self) -> None:
        self.tools: dict[str, Any] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler

    def __getattr__(self, _name):
        return lambda *a, **kw: None


class _Config:
    def __init__(self, home: str) -> None:
        self.home_dir = home

    def __getattr__(self, _name):
        return None


class _Runtime:
    """Enough runtime for a tool to run — the members they actually reach for."""

    def __init__(self, home: str) -> None:
        self.tool_registry = _Registry()
        self.config = _Config(home)
        self.memory = None
        self._session_brief = None
        self._session_id = "test-session"
        self._active_message_id = None
        self._tasks: list[_Task] = []
        self.home = home

    # -- the handful of methods tools call on a runtime ---------------------
    def create_task(self, title, description="", tags=None):
        task = _Task(title, description, tags)
        self._tasks.append(task)
        return task

    def list_tasks(self, *_a, **_kw):
        return list(self._tasks)

    def access_scope(self):
        return "home"

    def allowed_roots(self):
        return [self.home]

    def effective_project_path(self):
        return self.home

    def resolve_tool_path(self, path, **_kw):
        from pathlib import Path

        return Path(self.home) / str(path or ".")

    def _track_artifact(self, *_a, **_kw):
        return None

    def __getattr__(self, _name):
        return None


def _collect_tools(home: str) -> dict[str, tuple[str, Any]]:
    tools: dict[str, tuple[str, Any]] = {}
    for mod_info in pkgutil.walk_packages(core_pkg.__path__, prefix="remedy.core."):
        try:
            mod = importlib.import_module(mod_info.name)
        except Exception:  # pragma: no cover — import health has its own test
            continue
        for name, fn in vars(mod).items():
            if not (name.startswith("register_") and inspect.isfunction(fn)):
                continue
            params = list(inspect.signature(fn).parameters)
            if not params or params[0] != "runtime":
                continue
            rt = _Runtime(home)
            with contextlib.suppress(Exception):
                fn(rt)
            for tool_name, handler in rt.tool_registry.tools.items():
                if mod_info.name in NEVER_DRIVE_MODULES:
                    continue
                if tool_name.startswith(NEVER_DRIVE_PREFIXES):
                    continue
                if tool_name in NEVER_DRIVE_NAMES:
                    continue
                tools.setdefault(tool_name, (mod_info.name, handler))
    return tools


@pytest.fixture(scope="module")
def tools(tmp_path_factory):
    home = str(tmp_path_factory.mktemp("tool-drive"))
    # These are the real handlers, not fakes. Anything that resolves its home
    # from the environment rather than the runtime must land in tmp too, or a
    # test run edits the owner's actual state.
    prev = os.environ.get("REMEDY_HOME")
    os.environ["REMEDY_HOME"] = home
    try:
        yield _collect_tools(home)
    finally:
        if prev is None:
            os.environ.pop("REMEDY_HOME", None)
        else:
            os.environ["REMEDY_HOME"] = prev


def test_enough_tools_were_collected(tools):
    assert len(tools) > 100, f"only {len(tools)} tools found — the factories moved"


@pytest.mark.asyncio
async def test_no_tool_raises_a_programming_error_on_a_bare_call(tools):
    """Defaults only. Whatever a tool needs, asking for it must not explode."""
    broken: list[str] = []
    for name, (module, handler) in sorted(tools.items()):
        if name == "self_inject_round":
            # Drafts a patch against the tree; 60s is not enough on Linux CI.
            continue
        try:
            result = handler()
            if inspect.isawaitable(result):
                # Generous: some tools do real work on a bare call (screenshot
                # captures the screen and runs a local vision decode). We are
                # hunting exceptions, not slowness — but an unbounded wait
                # would hang the suite, so the ceiling stays.
                await asyncio.wait_for(result, timeout=60)
        except PROGRAMMING_ERRORS as exc:
            broken.append(f"{name} ({module}): {type(exc).__name__}: {exc}")
        except TimeoutError:
            broken.append(f"{name} ({module}): did not answer within 60s")
        except Exception:
            # A missing provider, an absent file, a refused action — all fine.
            pass

    assert not broken, "tools that break on a bare call:\n  " + "\n  ".join(broken)


@pytest.mark.parametrize(
    "expected",
    ["assistant_brief", "goal_list", "remind_me", "mail_status", "budget_get"],
)
def test_the_tools_the_manual_promises_are_registered(tools, expected):
    assert expected in tools, f"{expected} is documented but no longer registered"


def test_desktop_driving_tools_are_never_collected(tools):
    """Guard the guard: if this ever fails, the suite is about to take the wheel."""
    live = sorted(
        n
        for n in tools
        if n.startswith(NEVER_DRIVE_PREFIXES) or n in NEVER_DRIVE_NAMES
    )
    assert not live, (
        "these reach the real machine and must not be called by a test: "
        + ", ".join(live)
    )
