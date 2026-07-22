"""Skill exporter -- exports skills to various target formats.

Supports:
- agentskills.io native (SKILL.md + scripts/ + references/)
- Hermes format (SKILL.md with Hermes-specific frontmatter)
- OpenClaw/ClawHub format (skill.yaml manifest)
- ZIP archive (portable distribution)
"""

from __future__ import annotations

import shutil
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import yaml

from remedy.models import Skill


class SkillExporter:
    """Export skills to different formats for distribution or migration."""

    def __init__(self, output_dir: Path) -> None:
        self.output_dir = Path(output_dir)

    def export_native(self, skill: Skill) -> Path:
        """Export in native agentskills.io format."""
        dest = self.output_dir / skill.manifest.name
        dest.mkdir(parents=True, exist_ok=True)

        self._write_skill_md(skill, dest)
        self._copy_resources(skill, dest)

        return dest

    def export_hermes(self, skill: Skill) -> Path:
        """Export in Hermes-compatible format."""
        dest = self.output_dir / f"hermes_{skill.manifest.name}"
        dest.mkdir(parents=True, exist_ok=True)

        fm = {
            "name": skill.manifest.name,
            "description": skill.manifest.description,
            "version": skill.manifest.version,
            "author": skill.manifest.author or "Remedy",
            "tags": skill.manifest.tags,
            "hermes_version": "2.0",
            "remey_exported": datetime.now(timezone.utc).isoformat(),
        }

        skill_md_content = "---\n" + yaml.dump(fm, default_flow_style=False) + "---\n\n" + skill.instructions
        (dest / "SKILL.md").write_text(skill_md_content, encoding="utf-8")
        self._copy_resources(skill, dest)

        return dest

    def export_openclaw(self, skill: Skill) -> Path:
        """Export in OpenClaw/ClawHub format (skill.yaml)."""
        dest = self.output_dir / f"openclaw_{skill.manifest.name}"
        dest.mkdir(parents=True, exist_ok=True)

        manifest = {
            "name": skill.manifest.name,
            "title": skill.manifest.name,
            "description": skill.manifest.description,
            "version": skill.manifest.version,
            "author": skill.manifest.author or "Remedy",
            "tags": skill.manifest.tags,
            "instructions": skill.instructions,
            "type": "skill",
            "source": "remedy-export",
        }

        (dest / "skill.yaml").write_text(yaml.dump(manifest, default_flow_style=False), encoding="utf-8")
        self._copy_resources(skill, dest)

        return dest

    def export_zip(self, skill: Skill, format: str = "native") -> Path:
        """Export as a portable ZIP archive."""
        zip_path = self.output_dir / f"{skill.manifest.name}_{skill.manifest.version}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            if format == "native" or format == "hermes":
                self._write_skill_md_to_zip(zf, skill, format)
            elif format == "openclaw":
                self._write_openclaw_yaml_to_zip(zf, skill)
            self._add_resources_to_zip(zf, skill)
        return zip_path

    def _write_skill_md(self, skill: Skill, dest: Path) -> None:
        fm = self._build_frontmatter(skill)
        content = "---\n" + yaml.dump(fm, default_flow_style=False).strip() + "\n---\n\n" + skill.instructions
        (dest / "SKILL.md").write_text(content, encoding="utf-8")

    def _copy_resources(self, skill: Skill, dest: Path) -> None:
        if not skill.source_skill_dir:
            return
        base = Path(skill.source_skill_dir)
        for resource in skill.scripts + skill.references:
            src = base / resource
            if src.is_file():
                target = dest / resource
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src), str(target))

    def _build_frontmatter(self, skill: Skill) -> dict:
        return {
            "name": skill.manifest.name,
            "description": skill.manifest.description,
            "version": skill.manifest.version,
            "author": skill.manifest.author or "Remedy",
            "tags": skill.manifest.tags,
            "requires": skill.manifest.requires,
            "tools": skill.manifest.tools,
        }

    def _write_skill_md_to_zip(self, zf: zipfile.ZipFile, skill: Skill, format: str) -> None:
        if format == "hermes":
            fm = self._build_frontmatter(skill)
            fm["hermes_version"] = "2.0"
            fm["remey_exported"] = datetime.now(timezone.utc).isoformat()
        else:
            fm = self._build_frontmatter(skill)

        content = "---\n" + yaml.dump(fm, default_flow_style=False).strip() + "\n---\n\n" + skill.instructions
        zf.writestr(f"{skill.manifest.name}/SKILL.md", content)

    def _write_openclaw_yaml_to_zip(self, zf: zipfile.ZipFile, skill: Skill) -> None:
        manifest = {
            "name": skill.manifest.name,
            "description": skill.manifest.description,
            "version": skill.manifest.version,
            "instructions": skill.instructions,
            "type": "skill",
        }
        zf.writestr(
            f"{skill.manifest.name}/skill.yaml",
            yaml.dump(manifest, default_flow_style=False),
        )

    def _add_resources_to_zip(self, zf: zipfile.ZipFile, skill: Skill) -> None:
        if not skill.source_skill_dir:
            return
        base = Path(skill.source_skill_dir)
        for resource in skill.scripts + skill.references:
            src = base / resource
            if src.is_file():
                zf.write(str(src), f"{skill.manifest.name}/{resource}")
