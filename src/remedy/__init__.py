"""Remedy: personal AI partner — knowledge, design, code, and get-it-done."""

from __future__ import annotations

import re
import sys
from pathlib import Path


def _read_version_from_pyproject(path: Path) -> str | None:
    try:
        if not path.is_file():
            return None
        text = path.read_text(encoding="utf-8")
    except OSError:
        return None
    # Prefer the [project] version line (first bare version = "x.y.z" in file
    # is the package version in our layout).
    m = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    return m.group(1) if m else None


def _source_tree_pyproject() -> Path | None:
    """If this package is the repo checkout (…/src/remedy), return root pyproject."""
    try:
        pkg_dir = Path(__file__).resolve().parent  # …/src/remedy
        src_dir = pkg_dir.parent  # …/src
        root = src_dir.parent  # …/RemedyAI
        if src_dir.name == "src" and (root / "pyproject.toml").is_file():
            if (root / "src" / "remedy" / "__init__.py").is_file():
                return root / "pyproject.toml"
    except Exception:
        pass
    return None


def _get_version() -> str:
    """Resolve package version for CLI, API, About panel, and frozen builds.

    Order:
      1. Repo ``pyproject.toml`` when running from the source tree / editable install
         (avoids stale site-packages dist-info like 0.9.2 while source is 0.10.x)
      2. importlib.metadata for installed wheels
      3. Frozen / adjacent pyproject copies
    """
    # 1) Authoritative for dev / editable: the checkout we are executing from.
    src_pp = _source_tree_pyproject()
    if src_pp is not None:
        ver = _read_version_from_pyproject(src_pp)
        if ver:
            return ver

    # 2) Installed distribution metadata
    try:
        from importlib.metadata import version as _metadata_version

        return _metadata_version("remedy-ai")
    except Exception:
        pass

    # 3) Frozen / packaging fallbacks
    candidates = [
        Path(__file__).resolve().parents[2] / "pyproject.toml",
        Path(sys.executable).parent / "pyproject.toml",
        Path(sys.executable).parent / "_internal" / "pyproject.toml",
        Path(sys.prefix) / "pyproject.toml",
        Path(sys.prefix) / "_internal" / "pyproject.toml",
    ]
    try:
        if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
            candidates.insert(0, Path(sys._MEIPASS) / "pyproject.toml")
    except Exception:
        pass

    for candidate in candidates:
        ver = _read_version_from_pyproject(candidate)
        if ver:
            return ver

    return "0.0.0"


__version__ = _get_version()
__all__ = ["__version__"]
