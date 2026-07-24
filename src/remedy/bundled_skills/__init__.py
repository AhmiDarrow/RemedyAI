"""Bundled default skills shipped with Remedy.

These SKILL.md packages are discovered on every start and optionally
seeded into ``~/.remedy/skills`` so users can customize them.
"""

from __future__ import annotations

from pathlib import Path


def bundled_skills_dir() -> Path:
    """Return the package directory that contains default skill folders."""
    return Path(__file__).resolve().parent


def iter_bundled_skill_dirs() -> list[Path]:
    """List child dirs that contain a SKILL.md."""
    root = bundled_skills_dir()
    out: list[Path] = []
    for child in sorted(root.iterdir()):
        if child.is_dir() and (child / "SKILL.md").is_file():
            out.append(child)
    return out
