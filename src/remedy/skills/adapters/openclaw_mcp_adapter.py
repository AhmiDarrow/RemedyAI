"""Adapter for OpenClaw / MCP-based skills.

OpenClaw skills are typically distributed through ClawHub and may use
Markdown/YAML manifests. MCP-based skills are served through Model
Context Protocol servers. This adapter provides bridges for both.
"""

from __future__ import annotations

from pathlib import Path

from remedy.models import Skill, SkillKind
from remedy.skills.loader import (
    SkillLoadError,
    _build_manifest,
    _discover_bundled_resources,
    _parse_skill_md,
)


def load_openclaw_skill(skill_dir: str | Path) -> Skill:
    """Load an OpenClaw-format skill (ClawHub style).

    OpenClaw skills typically have a skill.yaml or skill.md manifest.
    We attempt to parse either format and normalize to Remedy's model.
    """
    root = Path(skill_dir).expanduser().resolve()
    if not root.is_dir():
        raise SkillLoadError(f"OpenClaw skill directory not found: {root}")

    manifest_file = root / "SKILL.md"
    yaml_file = root / "skill.yaml"
    md_file = root / "skill.md"

    if manifest_file.is_file():
        frontmatter, body = _parse_skill_md(manifest_file)
        manifest = _build_manifest(frontmatter, SkillKind.OPENCLAW, manifest_file)
    elif md_file.is_file():
        frontmatter, body = _parse_skill_md(md_file)
        # OpenClaw may use 'title' instead of 'name'
        if "name" not in frontmatter and "title" in frontmatter:
            frontmatter["name"] = frontmatter.pop("title")
        manifest = _build_manifest(frontmatter, SkillKind.OPENCLAW, md_file)
    elif yaml_file.is_file():
        import yaml

        raw = yaml_file.read_text(encoding="utf-8")
        frontmatter = yaml.safe_load(raw) or {}
        if "name" not in frontmatter and "title" in frontmatter:
            frontmatter["name"] = frontmatter.pop("title")
        manifest = _build_manifest(frontmatter, SkillKind.OPENCLAW, yaml_file)
        body = ""
    else:
        raise SkillLoadError(
            f"No SKILL.md, skill.md, or skill.yaml found in {root}"
        )

    scripts, refs = _discover_bundled_resources(root)

    return Skill(
        manifest=manifest,
        instructions=body,
        scripts=scripts,
        references=refs,
        source_skill_dir=str(root),
    )


def discover_openclaw_skills(base_dir: str | Path) -> list[Skill]:
    """Scan a directory for OpenClaw-format skills."""
    base = Path(base_dir).expanduser().resolve()
    if not base.is_dir():
        return []

    skills: list[Skill] = []
    for skill_dir in base.iterdir():
        if not skill_dir.is_dir() or skill_dir.name.startswith("."):
            continue
        try:
            skills.append(load_openclaw_skill(skill_dir))
        except SkillLoadError:
            continue

    return skills


def load_mcp_skill(server_name: str, tool_names: list[str]) -> Skill:
    """Create a synthetic Remedy skill wrapping an MCP server's tools.

    When an MCP server offers tools/resources/prompts, this produces
    a Skill that exposes them through Remedy's skill system.
    """
    import yaml

    synthetic_frontmatter = yaml.safe_dump({
        "name": f"mcp-{server_name}",
        "description": f"MCP server: {server_name}",
        "version": "1.0.0",
        "kind": "mcp",
        "tools": tool_names,
        "tags": ["mcp", server_name],
    })
    frontmatter, body = yaml.safe_load(synthetic_frontmatter), ""
    manifest = _build_manifest(
        frontmatter, SkillKind.MCP, Path(f"mcp://{server_name}/SKILL.md")
    )

    return Skill(
        manifest=manifest,
        instructions=f"MCP bridge to server '{server_name}'. "
        f"Available tools: {', '.join(tool_names)}.",
        scripts=[],
        references=[],
        source_skill_dir=None,
    )
