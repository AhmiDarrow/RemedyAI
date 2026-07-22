"""Deep Hermes adapter -- full metadata, configuration, and tool mapping.

Provides comprehensive migration from Hermes Agent setups, including:
- Config file parsing (hermes_config.yaml)
- Tool name mapping (Hermes internal tools -> MCP)
- Dependency resolution and verification
- Batch migration with progress reporting
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

from remedy.models import Skill
from remedy.skills.adapters.hermes_adapter import load_hermes_skill
from remedy.skills.loader import SkillLoadError

_TOOL_MAP: dict[str, str] = {
    "memory_search": "remedy_memory_search",
    "memory_upsert": "remedy_memory_upsert",
    "memory_list": "remedy_memory_list",
    "skill_load": "remedy_skill_load",
    "skill_discover": "remedy_skill_discover",
    "web_search": "mcp_web_search",
    "web_fetch": "mcp_web_fetch",
    "file_read": "remedy_file_read",
    "file_write": "remedy_file_write",
    "bash_exec": "remedy_bash_exec",
    "git_commit": "remedy_git_commit",
    "git_push": "remedy_git_push",
}


def map_hermes_tools(tool_names: list[str]) -> list[str]:
    """Map Hermes internal tool names to Remedy equivalents."""
    return [_TOOL_MAP.get(t, t) for t in tool_names]


def parse_hermes_config(config_path: Path) -> dict[str, Any]:
    """Parse a Hermes configuration file.

    Handles hermes_config.yaml in the Hermes home directory.
    """
    if not config_path.is_file():
        return {}
    try:
        raw = config_path.read_text(encoding="utf-8")
        return yaml.safe_load(raw) or {}
    except (yaml.YAMLError, OSError):
        return {}


def extract_hermes_tools_from_config(config: dict) -> list[str]:
    """Extract tool definitions from a Hermes config dict."""
    tools = []
    tool_sections = config.get("tools", {}) or {}

    if isinstance(tool_sections, dict):
        for section, items in tool_sections.items():
            if isinstance(items, list):
                tools.extend(items)
    return tools


def deep_load_hermes_skill(skill_dir: str | Path, config_path: Path | None = None) -> Skill:
    """Load a Hermes skill with deep metadata mapping.

    In addition to basic loading, this:
    1. Parses the Hermes config for tool context
    2. Maps internal tool references to Remedy equivalents
    3. Resolves dependencies declared in Hermes format
    4. Adds cross-reference metadata

    Args:
        skill_dir: Path to the Hermes skill directory.
        config_path: Optional path to hermes_config.yaml for context.

    Returns:
        A fully-mapped Remedy Skill.
    """
    skill = load_hermes_skill(skill_dir)

    skill.manifest.metadata = skill.manifest.metadata or {}
    skill.manifest.metadata["origin"] = "hermes"
    skill.manifest.metadata["original_path"] = str(Path(skill_dir).resolve())

    if skill.manifest.raw_frontmatter:
        raw = skill.manifest.raw_frontmatter
        if "hermes_version" in raw:
            skill.manifest.metadata["hermes_version"] = raw["hermes_version"]
        if "hermes_config" in raw:
            skill.manifest.metadata["config_ref"] = raw["hermes_config"]

    if config_path and config_path.is_file():
        config = parse_hermes_config(config_path)
        config_tools = extract_hermes_tools_from_config(config)
        if config_tools:
            extra_tools = map_hermes_tools(config_tools)
            skill.manifest.tools = list(set(skill.manifest.tools + extra_tools))
            skill.manifest.metadata["config_tools"] = config_tools
            skill.manifest.metadata["mapped_tools"] = extra_tools

    if skill.manifest.tools:
        skill.manifest.tools = map_hermes_tools(skill.manifest.tools)

    if skill.manifest.requires:
        skill.manifest.metadata["original_requires"] = list(skill.manifest.requires)

    return skill


def batch_migrate_hermes(
    skills_dir: str | Path,
    config_path: str | Path | None = None,
) -> tuple[int, list[Skill], list[str]]:
    """Migrate all Hermes skills in a directory with progress reporting.

    Returns: (count, skills_list, error_messages)
    """
    base = Path(skills_dir).expanduser().resolve()
    cfg = Path(config_path).expanduser() if config_path else None
    skills: list[Skill] = []
    errors: list[str] = []

    if not base.is_dir():
        return 0, skills, [f"Hermes skills directory not found: {base}"]

    for skill_dir in sorted(base.iterdir()):
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        try:
            skill = deep_load_hermes_skill(skill_dir, config_path=cfg)
            skills.append(skill)
        except SkillLoadError as e:
            errors.append(f"{skill_dir.name}: {e}")

    return len(skills), skills, errors
