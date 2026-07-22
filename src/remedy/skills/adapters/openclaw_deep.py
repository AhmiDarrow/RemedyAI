"""Deep OpenClaw / ClawHub adapter -- full manifest, MCP, and channel mapping.

Handles:
- ClawHub extended skill.yaml manifests
- MCP server configuration extraction
- Environment variable mapping
- Channel config migration (Telegram, Discord, etc.)
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import yaml

from remedy.models import Skill, SkillKind, SkillManifest, ToolDefinition, ToolSource
from remedy.skills.loader import SkillLoadError
from remedy.skills.tool_registry import ToolRegistry


def parse_clawhub_manifest(path: Path) -> dict[str, Any]:
    """Parse a ClawHub-style skill.yaml or claw.yaml manifest."""
    if not path.is_file():
        return {}
    try:
        raw = path.read_text(encoding="utf-8")
        return yaml.safe_load(raw) or {}
    except (yaml.YAMLError, OSError):
        return {}


def extract_mcp_servers(manifest: dict) -> list[dict]:
    """Extract MCP server definitions from a ClawHub manifest."""
    servers = []
    mcp_config = manifest.get("mcp", {}) or {}
    if isinstance(mcp_config, dict):
        for name, config in mcp_config.items():
            servers.append({
                "name": name,
                "command": config.get("command", ""),
                "args": config.get("args", []),
                "env": config.get("env", {}),
                "tools": config.get("tools", []),
            })
    return servers


def extract_channel_config(manifest: dict) -> dict[str, Any]:
    """Extract channel configurations (Telegram, Discord, etc.)."""
    channels = manifest.get("channels", {}) or {}
    if not isinstance(channels, dict):
        return {}
    return {k: v for k, v in channels.items() if isinstance(v, dict)}


def extract_env_vars(manifest: dict) -> dict[str, str]:
    """Extract required environment variables from a manifest."""
    env_vars = {}
    env_section = manifest.get("env", {}) or {}
    if isinstance(env_section, dict):
        for k, v in env_section.items():
            env_vars[str(k)] = str(v) if v is not None else ""
    return env_vars


def deep_load_openclaw_skill(skill_dir: str | Path) -> Skill:
    """Load an OpenClaw/ClawHub skill with full metadata extraction.

    Returns a Remedy Skill augmented with:
    - MCP server configurations as metadata
    - Channel configs for gateway setup
    - Environment variable requirements
    """
    root = Path(skill_dir).expanduser().resolve()
    if not root.is_dir():
        raise SkillLoadError(f"OpenClaw skill directory not found: {root}")

    candidates = [
        root / "SKILL.md",
        root / "skill.md",
        root / "skill.yaml",
        root / "claw.yaml",
    ]
    manifest_file = None
    for c in candidates:
        if c.is_file():
            manifest_file = c
            break

    if manifest_file is None:
        raise SkillLoadError(f"No manifest found in {root} (tried SKILL.md, skill.md, skill.yaml, claw.yaml)")

    if manifest_file.suffix in (".md", ".MD"):
        from remedy.skills.adapters.openclaw_mcp_adapter import load_openclaw_skill
        skill = load_openclaw_skill(root)
    else:
        manifest = parse_clawhub_manifest(manifest_file)
        if "name" not in manifest and "title" in manifest:
            manifest["name"] = manifest.pop("title")
        if "name" not in manifest:
            raise SkillLoadError(f"No 'name' field in {manifest_file}")

        skill_manifest = SkillManifest(
            name=manifest["name"],
            description=manifest.get("description", ""),
            version=str(manifest.get("version", "1.0.0")),
            author=manifest.get("author"),
            tags=manifest.get("tags", []) or [],
            kind=SkillKind.OPENCLAW,
            tools=manifest.get("tools", []) or [],
            path=str(root),
        )
        skill = Skill(
            manifest=skill_manifest,
            instructions=manifest.get("instructions", manifest.get("description", "")),
            source_skill_dir=str(root),
        )

    skill.manifest.metadata = skill.manifest.metadata or {}
    skill.manifest.metadata["origin"] = "openclaw"
    skill.manifest.metadata["original_path"] = str(root)

    if manifest_file.suffix in (".yaml", ".yml"):
        manifest = parse_clawhub_manifest(manifest_file)

        mcp_servers = extract_mcp_servers(manifest)
        if mcp_servers:
            skill.manifest.metadata["mcp_servers"] = mcp_servers

        channels = extract_channel_config(manifest)
        if channels:
            skill.manifest.metadata["channels"] = channels

        env_vars = extract_env_vars(manifest)
        if env_vars:
            skill.manifest.metadata["env_vars"] = env_vars

        config = manifest.get("config", {})
        if config:
            skill.manifest.metadata["config"] = config

    return skill


def register_mcp_tools_from_skill(
    registry: ToolRegistry,
    skill: Skill,
) -> int:
    """Register MCP tools declared in a skill's metadata into a ToolRegistry.

    Returns count of tools registered.
    """
    count = 0
    mcp_servers = skill.manifest.metadata.get("mcp_servers", []) or []
    for server in mcp_servers:
        server_name = server.get("name", "unknown")
        for tool_name in server.get("tools", []):
            registry.register(ToolDefinition(
                name=tool_name,
                description=f"MCP tool from {server_name}",
                source=ToolSource.MCP,
                uri=f"mcp://{server_name}/{tool_name}",
            ))
            count += 1
    return count
