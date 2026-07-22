"""Remedy: The self-improving, multi-channel AI agent framework."""

from __future__ import annotations

from pathlib import Path


def _get_version() -> str:
    try:
        from importlib.metadata import version as _metadata_version
        return _metadata_version("remedy-ai")
    except Exception:
        pass

    try:
        _pyproject = Path(__file__).resolve().parents[2] / "pyproject.toml"
        for line in _pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version"):
                _v = stripped.split("=", 1)[1].strip().strip('"')
                return _v
    except Exception:
        pass

    return "0.0.0"


__version__ = _get_version()
__all__ = ["__version__"]
