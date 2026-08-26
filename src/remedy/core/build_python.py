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


def is_usable_host_python(path: str) -> bool:
    """True for a real CPython or the ``py`` launcher; false for sidecar / Store stub.

    Host ``python`` mapping (dialect, ``resolve_which``, POSIX rewrites) must
    never point at ``remedy-desktop.exe`` — spawning it as Python relaunches
    serve on :7400 and drops the live chat.
    """
    p = (path or "").strip().strip("\"'")
    if not p:
        return False
    if _looks_like_sidecar(p):
        return False
    if "windowsapps" in p.lower().replace("\\", "/"):
        return False
    name = os.path.basename(p.replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name in {"uv"}:
        return False
    if name in {"py", "pyw"}:
        return True
    return _stem_is_python(p)


_LAUNCHER_RESOLVE_CACHE: dict[tuple[str, ...], str] = {}


def _single_exe(cmd: list[str] | None) -> str:
    """Concrete single-file CPython from an argv prefix ('' when none).

    ``['py', '-3']`` is resolved to the python.exe it launches (cached per
    process) — handing a shell bare ``py`` would run whatever py.ini /
    PY_PYTHON defaults to, which can be a different major version than the
    ``-3`` every argv consumer keeps.
    """
    if not cmd:
        return ""
    if len(cmd) == 1:
        return cmd[0] if is_usable_host_python(cmd[0]) else ""
    name = os.path.basename(cmd[0].replace("\\", "/")).lower()
    if name.endswith(".exe"):
        name = name[:-4]
    if name not in {"py", "pyw"}:
        return ""  # e.g. ['uv', 'run', 'python'] — no single exe for a shell
    key = tuple(cmd)
    if key in _LAUNCHER_RESOLVE_CACHE:
        return _LAUNCHER_RESOLVE_CACHE[key]
    exe = ""
    with suppress(Exception):
        import subprocess

        from remedy.execution.process import hidden_subprocess_kwargs

        r = subprocess.run(
            [*cmd, "-c", "import sys;print(sys.executable)"],
            capture_output=True,
            text=True,
            timeout=15,
            **hidden_subprocess_kwargs(),
        )
        out = (r.stdout or "").strip()
        if r.returncode == 0 and out:
            cand = out.splitlines()[-1].strip()
            if cand and os.path.isfile(cand) and is_usable_host_python(cand):
                exe = cand
    _LAUNCHER_RESOLVE_CACHE[key] = exe
    return exe


def host_python_executable() -> str:
    """Single-file CPython for host ``python``. Never the sidecar or bare ``py``.

    Empty string means none — callers must skip or fail honestly, not fall back
    to ``sys.executable`` when frozen.
    """
    exe = _single_exe(python_cmd_for_subprocess())
    if exe:
        return exe
    with suppress(Exception):
        from remedy.core.workspace_tools.shell import resolve_python_interpreter

        exe = _single_exe(resolve_python_interpreter())
    return exe or ""


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
        which = shutil.which(name)
        if not which:
            continue
        low = which.lower()
        if "windowsapps" in low or _looks_like_sidecar(which):
            continue
        if _stem_is_python(which):
            return [which]

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
    return "usage: remedy" in t or (
        t.strip().startswith("remedy: error") and "choice" in t
    )
