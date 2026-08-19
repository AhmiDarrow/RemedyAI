"""A whole bug class ruff does not catch: using a module you never imported.

``api.py`` did ``contextlib.suppress(...)`` while importing only
``from contextlib import suppress``. Ruff's F821 passes it, mypy catches it, and
at runtime it is a NameError that fires only when that line executes — which for
the reminder→messenger bridge meant every push failed silently inside an
enclosing ``suppress``.

This walks every source file and asserts that any ``module.attr`` access names a
module the file actually bound.
"""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "remedy"

#: Only names that are unambiguously stdlib modules, so a local variable called
#: e.g. ``config`` or ``memory`` can never be mistaken for one.
WATCHED = frozenset(
    {
        "argparse", "ast", "asyncio", "base64", "contextlib", "copy", "csv",
        "ctypes", "datetime", "difflib", "functools", "glob", "hashlib", "hmac",
        "importlib", "inspect", "io", "ipaddress", "itertools", "json",
        "logging", "math", "mimetypes", "os", "pathlib", "pickle", "platform",
        "random", "re", "secrets", "shlex", "shutil", "signal", "socket",
        "sqlite3", "stat", "string", "struct", "subprocess", "sys", "tarfile",
        "tempfile", "textwrap", "threading", "time", "tomllib", "traceback",
        "types", "typing", "unicodedata", "urllib", "uuid", "warnings",
        "weakref", "zipfile",
    }
)


def _bound_module_names(tree: ast.AST) -> set[str]:
    """Every name the file could plausibly have bound to a module object."""
    bound: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            for alias in node.names:
                bound.add(alias.asname or alias.name)
        elif isinstance(node, ast.Name) and isinstance(node.ctx, ast.Store):
            bound.add(node.id)  # e.g. `os = _pick_os()`, or a shadowing local
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            args = node.args
            for a in (
                *args.posonlyargs, *args.args, *args.kwonlyargs,
                args.vararg, args.kwarg,
            ):
                if a is not None:
                    bound.add(a.arg)
        elif isinstance(node, ast.Global):
            bound.update(node.names)
    return bound


def _watched_module_uses(tree: ast.AST) -> set[str]:
    return {
        node.value.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name)
        and node.value.id in WATCHED
    }


def _sources() -> list[Path]:
    return sorted(p for p in SRC.rglob("*.py") if "__pycache__" not in p.parts)


def test_there_are_sources_to_check():
    assert len(_sources()) > 100


@pytest.mark.skipif(sys.version_info < (3, 12), reason="ast.parse target")
def test_no_module_is_used_without_being_imported():
    offenders: list[str] = []
    for path in _sources():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        except (OSError, SyntaxError):  # pragma: no cover — not this test's job
            continue
        missing = _watched_module_uses(tree) - _bound_module_names(tree)
        offenders.extend(
            f"{path.relative_to(SRC.parents[1])}: uses {name}.* but never binds {name}"
            for name in sorted(missing)
        )
    assert not offenders, "\n".join(offenders)
