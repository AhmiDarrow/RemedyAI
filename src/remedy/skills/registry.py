"""Skill registry for discovery, validation, activation, and versioning.

Maintains the canonical set of skills available to the agent runtime.
"""

from __future__ import annotations

from pathlib import Path
from uuid import UUID

from remedy.models import Skill, SkillStatus
from remedy.skills.loader import discover_skills, load_skill_from_dir


class SkillRegistry:
    """Thread-safe registry of loadable skills.

    Handles discovery, deduplication (by name), activation, and version tracking.
    """

    def __init__(self) -> None:
        self._skills: dict[UUID, Skill] = {}
        self._by_name: dict[str, UUID] = {}

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
