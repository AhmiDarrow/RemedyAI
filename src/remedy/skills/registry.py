"""Skill registry for discovery, validation, activation, and versioning.

Maintains the canonical set of skills available to the agent runtime.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from remedy.models import Skill, SkillStatus
from remedy.skills.loader import discover_skills, load_skill_from_dir


def _merge_bundled_local_frontmatter(
    *,
    bundled_skill_md: Path,
    user_skill_md: Path,
) -> None:
    """If user SKILL.md has no ``local:`` block but bundled does, inject it.

    Preserves the rest of the user's skill (instructions/tools) so upgrades add
    portable discovery without resetting personalizations.
    """
    import re

    import yaml

    if not bundled_skill_md.is_file() or not user_skill_md.is_file():
        return
    b_raw = bundled_skill_md.read_text(encoding="utf-8")
    u_raw = user_skill_md.read_text(encoding="utf-8")
    b_m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", b_raw, re.DOTALL)
    u_m = re.match(r"^---\s*\n(.*?)\n---\s*\n?", u_raw, re.DOTALL)
    if not b_m or not u_m:
        return
    try:
        b_fm = yaml.safe_load(b_m.group(1)) or {}
        u_fm = yaml.safe_load(u_m.group(1)) or {}
    except yaml.YAMLError:
        return
    if not isinstance(b_fm, dict) or not isinstance(u_fm, dict):
        return
    if "local" not in b_fm or "local" in u_fm:
        return
    u_fm["local"] = b_fm["local"]
    # Prefer listing local_discover alongside skill tools when bundled does.
    b_tools = list(b_fm.get("tools") or [])
    u_tools = list(u_fm.get("tools") or [])
    for t in b_tools:
        if t not in u_tools:
            u_tools.append(t)
    if u_tools:
        u_fm["tools"] = u_tools
    body = u_raw[u_m.end() :]
    new_fm = yaml.safe_dump(u_fm, sort_keys=False, allow_unicode=True).strip()
    user_skill_md.write_text(f"---\n{new_fm}\n---\n{body}", encoding="utf-8")


class SkillRegistry:
    """Thread-safe registry of loadable skills.

    Handles discovery, deduplication (by name), activation, and version tracking.
    """

    def __init__(self, auto_discover: bool | Path | str = False) -> None:
        self._skills: dict[UUID, Skill] = {}
        self._by_name: dict[str, UUID] = {}
        if auto_discover is True:
            self.discover_defaults()
        elif auto_discover:
            self.discover(auto_discover)

    def discover_defaults(self, home_dir: str | Path | None = None) -> int:
        """Discover skills from bundled + user locations.

        Order (later names win on collision):
        1. Package bundled defaults (``remedy/bundled_skills``)
        2. Repo ``./skills`` if present (dev tree)
        3. ``$REMEDY_HOME/skills`` or ``~/.remedy/skills`` (user overrides)
        """
        import os
        import shutil

        from remedy.bundled_skills import bundled_skills_dir

        # 1) Always load shipped defaults so a fresh install is useful.
        bundled = bundled_skills_dir()
        if bundled.is_dir():
            self.discover(bundled, recurse=True)

        # 2) Dev tree skills/
        cwd_skills = Path.cwd() / "skills"
        if cwd_skills.is_dir():
            self.discover(cwd_skills, recurse=True)

        # 3) User home skills (custom + seeded)
        if home_dir is not None:
            user_skills = Path(home_dir).expanduser() / "skills"
        else:
            env_home = os.environ.get("REMEDY_HOME")
            user_skills = (
                Path(env_home).expanduser() / "skills"
                if env_home
                else Path("~/.remedy/skills").expanduser()
            )
        user_skills.mkdir(parents=True, exist_ok=True)
        # Seed missing bundled skills into user dir (never overwrite customizations).
        # If a seeded skill is outdated and lacks a ``local:`` discovery block that
        # the bundled copy now has, merge only that frontmatter key so ambient
        # discovery works without clobbering user edits to instructions.
        if bundled.is_dir():
            for child in bundled.iterdir():
                if not child.is_dir() or not (child / "SKILL.md").is_file():
                    continue
                dest = user_skills / child.name
                if not dest.exists():
                    try:
                        shutil.copytree(child, dest)
                    except OSError:
                        pass
                else:
                    try:
                        _merge_bundled_local_frontmatter(
                            bundled_skill_md=child / "SKILL.md",
                            user_skill_md=dest / "SKILL.md",
                        )
                    except OSError:
                        pass
        if user_skills.is_dir():
            self.discover(user_skills, recurse=True)

        # Activate everything discovered so they show as ready.
        for skill in self.skills:
            if skill.manifest.status.value in ("discovered", "validated", "active"):
                skill.manifest.status = SkillStatus.ACTIVE
        return self.count

    def summary_lines(self, *, limit: int = 50) -> list[str]:
        """Human-readable skill list for prompts and slash commands."""
        lines: list[str] = []
        for skill in sorted(self.skills, key=lambda s: s.manifest.name.lower())[:limit]:
            m = skill.manifest
            extra = ""
            raw = m.raw_frontmatter or {}
            local = raw.get("local") if isinstance(raw, dict) else None
            if isinstance(local, dict):
                svc_ids = []
                for s in local.get("services") or []:
                    if isinstance(s, dict) and s.get("id"):
                        svc_ids.append(str(s["id"]))
                if svc_ids:
                    extra = f" [local: {', '.join(svc_ids)} — use local_discover/comfyui tools, not disk hunts]"
            lines.append(f"- **{m.name}**: {m.description}{extra}")
        return lines

    # -- properties ----------------------------------------------------------

    @property
    def skills(self) -> list[Skill]:
        return list(self._skills.values())

    @property
    def active(self) -> list[Skill]:
        return [s for s in self._skills.values() if s.manifest.status == SkillStatus.ACTIVE]

    @property
    def count(self) -> int:
        return len(self._skills)

    # -- registration --------------------------------------------------------

    def register(self, skill: Skill) -> Skill:
        """Register or update a skill. If a skill with the same name exists,
        the newer one replaces the old (by UUID)."""
        existing_id = self._by_name.get(skill.manifest.name)
        if existing_id is not None:
            del self._skills[existing_id]

        self._skills[skill.id] = skill
        self._by_name[skill.manifest.name] = skill.id
        return skill

    def discover(self, *paths: str | Path, recurse: bool = True) -> int:
        """Scan directories for SKILL.md files and register them.

        Returns the number of newly discovered skills.
        """
        discovered = 0
        for raw_path in paths:
            root = Path(raw_path).expanduser().resolve()
            if not root.exists():
                continue
            for skill in discover_skills(root, recurse=recurse):
                self.register(skill)
                discovered += 1
        return discovered

    def load_single(self, path: str | Path) -> Skill:
        """Load and register a single skill from its directory."""
        skill = load_skill_from_dir(Path(path))
        return self.register(skill)

    # -- activation ----------------------------------------------------------

    def activate(self, name: str) -> bool:
        skill_id = self._by_name.get(name)
        if skill_id is None:
            return False
        self._skills[skill_id].manifest.status = SkillStatus.ACTIVE
        return True

    def deactivate(self, name: str) -> bool:
        skill_id = self._by_name.get(name)
        if skill_id is None:
            return False
        self._skills[skill_id].manifest.status = SkillStatus.DISABLED
        return True

    def validate_all(self) -> tuple[int, int]:
        """Mark all currently ACTIVE skills as VALIDATED. Returns (total, validated)."""
        count = 0
        for skill in self._skills.values():
            if skill.manifest.status == SkillStatus.ACTIVE:
                skill.manifest.status = SkillStatus.VALIDATED
                count += 1
        return len(self._skills), count

    # -- lookup --------------------------------------------------------------

    def get(self, name_or_id: str | UUID) -> Skill | None:
        if isinstance(name_or_id, UUID):
            return self._skills.get(name_or_id)
        skill_id = self._by_name.get(name_or_id)
        if skill_id is None:
            return None
        return self._skills.get(skill_id)

    def search(self, query: str) -> list[Skill]:
        """Simple substring search across skill names, descriptions, and tags."""
        q = query.lower()
        results: list[Skill] = []
        for skill in self._skills.values():
            m = skill.manifest
            if (
                q in m.name.lower()
                or q in m.description.lower()
                or any(q in t.lower() for t in m.tags)
            ):
                results.append(skill)
        return results

    def remove(self, name: str) -> bool:
        skill_id = self._by_name.pop(name, None)
        if skill_id is None:
            return False
        self._skills.pop(skill_id, None)
        return True

    def clear(self) -> None:
        self._skills.clear()
        self._by_name.clear()
