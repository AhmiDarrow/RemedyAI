"""What the model is told a tool takes must be what the tool actually takes.

A schema that advertises a parameter the handler does not have is a ``TypeError``
the first time the model uses it — and the model has no way to know. A handler
with a required parameter the schema never mentions can never be called at all.
Neither shows up in a unit test of the tool itself, because both live in the gap
between the two.

226 builtin tools go through here.
"""

from __future__ import annotations

import contextlib
import importlib
import inspect
import pkgutil
import tempfile
from pathlib import Path

import remedy.core as core

_HOME = Path(tempfile.mkdtemp(prefix="remedy-toolcheck-"))


class _RecordingRegistry:
    def __init__(self) -> None:
        self.seen: list[tuple[str, object, dict]] = []

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.seen.append((name, handler, parameters or {}))
        return None

    def register_builtin(self, name, description, parameters=None):
        return None

    def __getattr__(self, _name):  # any other registry call is a no-op
        return lambda *a, **kw: None


class _StubConfig:
    home_dir = str(_HOME)

    def __getattr__(self, _name):
        return None


class _StubRuntime:
    def __init__(self) -> None:
        self.tool_registry = _RecordingRegistry()
        self.config = _StubConfig()

    def __getattr__(self, _name):
        return None


def _registrars():
    out = []
    for mod_info in pkgutil.walk_packages(core.__path__, prefix="remedy.core."):
        try:
            mod = importlib.import_module(mod_info.name)
        except Exception:  # pragma: no cover — import health has its own test
            continue
        for fname, fn in vars(mod).items():
            if not (fname.startswith("register_") and inspect.isfunction(fn)):
                continue
            params = list(inspect.signature(fn).parameters)
            if params and params[0] == "runtime":
                out.append((mod_info.name, fname, fn))
    return out


def _collect():
    tools = []
    for modname, _fname, fn in _registrars():
        rt = _StubRuntime()
        # A registrar may need more of a runtime than this stub offers; whatever
        # it managed to register before giving up still counts.
        with contextlib.suppress(Exception):
            fn(rt)
        tools.extend((modname, *t) for t in rt.tool_registry.seen)
    return tools


def test_enough_tools_are_reachable_for_this_check_to_mean_something():
    assert len(_collect()) > 150


def test_every_schema_matches_the_handler_behind_it():
    problems: list[str] = []
    for modname, name, handler, schema in _collect():
        try:
            sig = inspect.signature(handler)
        except (TypeError, ValueError):  # pragma: no cover
            continue

        takes_kwargs = any(
            p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
        named = {
            n
            for n, p in sig.parameters.items()
            if p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        }
        required_by_handler = {
            n
            for n, p in sig.parameters.items()
            if p.default is inspect.Parameter.empty
            and p.kind in (p.POSITIONAL_OR_KEYWORD, p.KEYWORD_ONLY)
        }
        advertised = set((schema or {}).get("properties") or {})
        required_by_schema = set((schema or {}).get("required") or [])

        ghosts = advertised - named
        if ghosts and not takes_kwargs:
            problems.append(
                f"{name} ({modname}): schema advertises {sorted(ghosts)}, "
                "which the handler has no parameter for — TypeError the first "
                "time the model uses it"
            )
        unreachable = required_by_handler - advertised
        if unreachable:
            problems.append(
                f"{name} ({modname}): handler requires {sorted(unreachable)} but "
                "the schema never mentions it — the model can never supply it"
            )
        undefined = required_by_schema - advertised
        if undefined:
            problems.append(
                f"{name} ({modname}): schema marks {sorted(undefined)} required "
                "without defining it"
            )

    assert not problems, "\n".join(problems)
