"""Remedy: personal AI partner — knowledge, design, code, and get-it-done."""

from __future__ import annotations

import sys
from pathlib import Path


def _get_version() -> str:
    try:
        from importlib.metadata import version as _metadata_version
        return _metadata_version("remedy-ai")
    except Exception:
        pass

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
        try:
            if candidate.exists():
                for line in candidate.read_text(encoding="utf-8").splitlines():
                    stripped = line.strip()
                    if stripped.startswith("version"):
                        _v = stripped.split("=", 1)[1].strip().strip('"')
                        return _v
        except Exception:
            continue

    return "0.0.0"


__version__ = _get_version()
__all__ = ["__version__"]
