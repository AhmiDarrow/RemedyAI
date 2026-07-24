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
from datetime import UTC, datetime
from pathlib import Path

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
            "remedy_exported": datetime.now(UTC).isoformat(),
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
        fm = {
            "name": skill.manifest.name,
            "description": skill.manifest.description,
            "version": skill.manifest.version,
            "author": skill.manifest.author or "Remedy",
            "tags": skill.manifest.tags,
            "requires": skill.manifest.requires,
            "tools": skill.manifest.tools,
        }
        if skill.manifest.status:
            fm["status"] = (
                skill.manifest.status.value
                if hasattr(skill.manifest.status, "value")
                else str(skill.manifest.status)
            )
        if skill.manifest.metadata:
            fm["metadata"] = dict(skill.manifest.metadata)
        return fm

    def _write_skill_md_to_zip(self, zf: zipfile.ZipFile, skill: Skill, format: str) -> None:
        if format == "hermes":
            fm = self._build_frontmatter(skill)
            fm["hermes_version"] = "2.0"
            fm["remedy_exported"] = datetime.now(UTC).isoformat()
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

    def export_pack(self, skills: list[Skill]) -> Path:
        """Export multiple skills into one portable ZIP (native agentskills format)."""
        stamp = datetime.now(UTC).strftime("%Y%m%d")
        zip_path = self.output_dir / f"remedy-skills-pack-{stamp}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
            zf.writestr(
                "PACK.json",
                yaml.dump(
                    {
                        "format": "remedy-skill-pack",
                        "version": 1,
                        "exported_at": datetime.now(UTC).isoformat(),
                        "skills": [s.manifest.name for s in skills],
                    },
                    default_flow_style=False,
                ),
            )
            for skill in skills:
                self._write_skill_md_to_zip(zf, skill, "native")
                self._add_resources_to_zip(zf, skill)
        return zip_path

    def import_pack_quarantine(
        self,
        zip_path: Path,
        dest_skills_dir: Path,
    ) -> list[Skill]:
        """Import skills from a pack ZIP into dest with quarantine metadata.

        Imported skills start DISCOVERED + quarantine=true until the user
        promotes them via the Skills panel / API.
        """
        from remedy.models import SkillKind, SkillManifest, SkillStatus
        from remedy.skills.loader import load_skill_from_dir

        dest_skills_dir = Path(dest_skills_dir).expanduser()
        dest_skills_dir.mkdir(parents=True, exist_ok=True)
        extract_root = self.output_dir / "_import_extract"
        if extract_root.exists():
            shutil.rmtree(extract_root, ignore_errors=True)
        extract_root.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(zip_path, "r") as zf:
            zf.extractall(extract_root)

        imported: list[Skill] = []
        # Find SKILL.md files under extract root
        for skill_md in extract_root.rglob("SKILL.md"):
            try:
                skill = load_skill_from_dir(skill_md.parent)
            except Exception:
                continue
            name = skill.manifest.name or skill_md.parent.name
            target = dest_skills_dir / name
            if target.exists():
                # Do not clobber; place alongside as name-imported
                target = dest_skills_dir / f"{name}-imported"
                skill.manifest.name = target.name
            shutil.copytree(skill_md.parent, target, dirs_exist_ok=True)
            # Mark quarantine on disk frontmatter
            meta = dict(skill.manifest.metadata or {})
            meta["quarantine"] = True
            meta["trust"] = "imported"
            meta["imported_at"] = datetime.now(UTC).isoformat()
            skill.manifest.metadata = meta
            skill.manifest.status = SkillStatus.DISCOVERED
            skill.manifest.kind = SkillKind.NATIVE
            skill.source_skill_dir = str(target.resolve())
            skill.manifest.path = str(target.resolve())
            # Rewrite SKILL.md with quarantine flags
            fm = self._build_frontmatter(skill)
            fm["status"] = SkillStatus.DISCOVERED.value
            fm["metadata"] = meta
            body = skill.instructions or ""
            content = (
                "---\n"
                + yaml.dump(fm, default_flow_style=False, sort_keys=False).strip()
                + "\n---\n\n"
                + body
            )
            (target / "SKILL.md").write_text(content, encoding="utf-8")
            try:
                skill = load_skill_from_dir(target)
            except Exception:
                pass
            imported.append(skill)
        return imported
