"""Skills Library — signed remote catalog + install into quarantine."""

from __future__ import annotations

from remedy.skills.library.catalog import (
    SkillCatalogEntry,
    SkillsCatalog,
    get_skills_catalog,
    search_catalog,
)
from remedy.skills.library.install import install_skill_from_catalog
from remedy.skills.library.suggest import (
    LibraryHit,
    rank_library_skills,
    suggest_library_skill,
)

__all__ = [
    "SkillCatalogEntry",
    "SkillsCatalog",
    "LibraryHit",
    "get_skills_catalog",
    "search_catalog",
    "install_skill_from_catalog",
    "rank_library_skills",
    "suggest_library_skill",
]
