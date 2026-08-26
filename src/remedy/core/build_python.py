"""Real CPython for build oracles — never the frozen/sidecar Remedy exe.

Build gates (import dry-run, gate tower pytest/mypy, mutant score, reducer
oracles) must spawn a real interpreter. In Desktop, ``sys.executable`` is
``remedy-desktop.exe`` / the ``remedy`` CLI; feeding it ``-c`` or ``-m pytest``
prints usage (``invalid choice``) and the machine then screams
``[IMPORT DRY-RUN · RED]`` at healthy modules. Models thrash fixing imports
that are not broken.

Resolution order:
  1. Project ``.venv`` / ``uv run python`` when *root* is given
  2. ``resolve_python_interpreter()`` (REMEDY_PYTHON, PATH, Windows installs)
  3. Non-frozen ``sys.executable`` only when its stem is actually ``python*``
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from contextlib import suppress
from pathlib import Path

_PY_STEM_RE = re.compile(r"^python(?:w)?\d*(?:\.\d+)*$", re.I)


def _stem_is_python(path: str) -> bool:
    name = os.path.basename((path or "").strip().strip("\"'").replace("\\", "/"))
    if name.lower().endswith(".exe"):
        name = name[:-4]
    return bool(_PY_STEM_RE.match(name))


def _looks_like_sidecar(path: str) -> bool:
    name = os.path.basename((path or "").strip().replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    return "remedy" in name and not _stem_is_python(path)


def python_cmd_for_subprocess(root: Path | str | None = None) -> list[str]:
    """Return argv prefix for a real Python (possibly ``[uv, run, python]``).

    Empty list means no usable interpreter — callers must soft-fail, not fall
    back to the sidecar.
    """
    root_p: Path | None = None
    if root is not None:
        with suppress(Exception):
            root_p = Path(root)
            if root_p.is_file():
                root_p = root_p.parent

    if root_p is not None:
        with suppress(Exception):
            from remedy.core.agent_analysis_tools import project_python

            py, _src, _notes = project_python(root_p)
            if py:
                # uv run python is fine; a bare path must not be the sidecar
                if len(py) == 1 and _looks_like_sidecar(py[0]):
                    pass
                else:
                    return list(py)

    with suppress(Exception):
        from remedy.core.workspace_tools.shell import resolve_python_interpreter

        found = resolve_python_interpreter()
        if found:
            if len(found) == 1 and _looks_like_sidecar(found[0]):
                pass
            else:
                return list(found)

    # PATH fallbacks (resolve_python_interpreter already covers most; belt+suspenders)
    for name in ("python", "python3"):
        found = shutil.which(name)
        if not found:
            continue
        low = found.lower()
        if "windowsapps" in low or _looks_like_sidecar(found):
            continue
        if _stem_is_python(found):
            return [found]

    exe = sys.executable or ""
    if (
        exe
        and not getattr(sys, "frozen", False)
        and _stem_is_python(exe)
        and not _looks_like_sidecar(exe)
    ):
        return [exe]
    return []


def is_sidecar_spawn_error(text: str) -> bool:
    """True when stderr looks like the Remedy CLI ate a Python ``-c`` / ``-m``."""
    t = (text or "").lower()
    if not t:
        return False
    markers = (
        "invalid choice",
        "argument command",
        "usage: remedy",
        "remedy: error",
        "unrecognized arguments",
    )
    if any(m in t for m in markers) and (
        "importlib" in t or "sys.path" in t or "-c" in t or "pytest" in t or "mypy" in t
    ):
        return True
    # bare CLI usage dump without the python snippet still counts when remedy-branded
    if "usage: remedy" in t or (t.strip().startswith("remedy: error") and "choice" in t):
        return True
    return False
