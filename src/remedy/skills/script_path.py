"""Jail skill scripts under ``<skill_dir>/scripts`` (agent, MCP, CLI)."""

from __future__ import annotations

from pathlib import Path


class SkillScriptJailError(ValueError):
    """Requested script is absolute or escapes ``skill_dir/scripts``."""


def _is_absolute_script_name(name: str) -> bool:
    s = (name or "").strip()
    if not s:
        return False
    p = Path(s)
    if p.is_absolute() or bool(p.drive):
        return True
    if s.startswith(("/", "\\")):
        return True
    if len(s) >= 2 and s[1] == ":":
        return True
    return s.startswith("\\\\") or s.startswith("//")


def resolve_jailed_skill_script(skill_dir: str | Path, script_name: str) -> Path:
    """Resolve *script_name* under ``skill_dir/scripts``.

    Rejects absolute names (including Windows ``C:\\…`` after ``Path /``),
    ``..`` traversal, and anything that does not stay under ``scripts/``.
    """
    raw = (script_name or "").strip()
    if not raw:
        raise SkillScriptJailError("empty script name")
    if _is_absolute_script_name(raw):
        raise SkillScriptJailError("absolute script path rejected")

    norm = raw.replace("\\", "/")
    parts = [p for p in norm.split("/") if p and p != "."]
    if not parts or any(p == ".." for p in parts):
        raise SkillScriptJailError("script path escapes scripts/")
    if parts[0] == "scripts":
        parts = parts[1:]
    if not parts or any(p == ".." for p in parts):
        raise SkillScriptJailError("script path escapes scripts/")
    rel = "/".join(parts)
    if _is_absolute_script_name(rel):
        raise SkillScriptJailError("absolute script path rejected")

    base = Path(skill_dir).resolve()
    scripts_root = (base / "scripts").resolve()
    candidate = (scripts_root / rel).resolve()
    try:
        candidate.relative_to(scripts_root)
    except ValueError as exc:
        raise SkillScriptJailError("script path escapes scripts/") from exc
    return candidate
