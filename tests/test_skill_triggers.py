"""Frontmatter ``triggers:`` and the reference budget of skill_body."""
from __future__ import annotations

from pathlib import Path

import pytest

from remedy.models import SkillStatus
from remedy.skills.loader import SkillLoadError, load_skill_from_dir
from remedy.skills.registry import SkillRegistry


def _skill(tmp_path: Path, name: str, *, triggers: list[str] | None = None, refs: dict[str, str] | None = None,
           status: str = "active", extra_fm: str = "") -> Path:
    d = tmp_path / name
    d.mkdir()
    fm = [f"name: {name}", f"description: the {name} skill", "version: 1.0.0", f"status: {status}"]
    if triggers:
        fm.append("triggers:")
        # Single-quoted YAML scalars keep regex backslashes verbatim.
        fm += [f"  - '{t}'" for t in triggers]
    if extra_fm:
        fm.append(extra_fm)
    (d / "SKILL.md").write_text("---\n" + "\n".join(fm) + "\n---\n\n# " + name + "\n\nBody of the skill here.\n", encoding="utf-8")
    for rel, text in (refs or {}).items():
        p = d / "references" / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return d


def test_triggers_are_parsed_and_validated(tmp_path):
    d = _skill(tmp_path, "godot-4", triggers=[r"\b(godot|gdscript)\b", r"\.tscn\b"])
    s = load_skill_from_dir(d)
    assert s.manifest.triggers == [r"\b(godot|gdscript)\b", r"\.tscn\b"]
    bad = _skill(tmp_path, "broken", triggers=[r"(unclosed"])
    with pytest.raises(SkillLoadError):
        load_skill_from_dir(bad)


def test_registry_returns_triggered_skills_earliest_match_first(tmp_path):
    reg = SkillRegistry()
    for d in (
        _skill(tmp_path, "godot-4", triggers=[r"\bgodot\b"]),
        _skill(tmp_path, "game-dev-studio", triggers=[r"\b(make|build)\s+(a\s+)?\w*\s*game\b|\bplatformer\b"]),
        _skill(tmp_path, "unity", triggers=[r"\bunity\b"]),
        _skill(tmp_path, "quiet", triggers=None),
    ):
        reg.register(load_skill_from_dir(d))
    assert reg.triggered_skills("make a platformer in godot") == ["game-dev-studio", "godot-4"]
    assert reg.triggered_skills("Godot: fix the autoload") == ["godot-4"]
    assert reg.triggered_skills("refactor the parser") == []
    assert reg.triggered_skills("") == []


def test_disabled_and_quarantined_skills_never_trigger(tmp_path):
    reg = SkillRegistry()
    reg.register(load_skill_from_dir(_skill(tmp_path, "switched-off", triggers=[r"\bgodot\b"], status="disabled")))
    reg.register(load_skill_from_dir(_skill(tmp_path, "quar", triggers=[r"\bgodot\b"], extra_fm="quarantine: true")))
    reg.register(load_skill_from_dir(_skill(tmp_path, "switched-on", triggers=[r"\bgodot\b"])))
    assert reg.get("switched-off").manifest.status == SkillStatus.DISABLED
    assert reg.triggered_skills("godot please") == ["switched-on"]


def test_skill_body_lists_every_reference_and_inlines_index_first(tmp_path):
    refs = {"zz-last.md": "last " * 10, "INDEX.md": "INDEX: zz-last.md — when stuck\n", "aa-first.md": "x" * 9000}
    reg = SkillRegistry()
    reg.register(load_skill_from_dir(_skill(tmp_path, "godot-4", refs=refs)))
    body = reg.skill_body("godot-4", include_references=True)
    assert "**Path:**" in body and str(tmp_path / "godot-4") in body
    listing = body[body.index("## References (on disk)"):]
    assert "references/INDEX.md" in listing and "references/zz-last.md" in listing
    first = body.index("## Reference: references/INDEX.md")
    assert first < body.index("## Reference: references/aa-first.md")
    assert "truncated at" in body  # the 9000-char ref is capped, not dropped
    # No 'more references not inlined' note when everything fit.
    assert "not inlined" not in body


def test_skill_body_without_references_flag_stays_lean(tmp_path):
    reg = SkillRegistry()
    reg.register(load_skill_from_dir(_skill(tmp_path, "godot-4", refs={"INDEX.md": "map"})))
    assert "## Reference" not in reg.skill_body("godot-4")


# --- domain packs must not crowd the stage-1 catalog ---------------------------


def test_domain_packs_only_compete_for_catalog_slots_on_their_own_asks(tmp_path):
    """With dozens of field packs installed, an unrelated turn must still see the
    general procedures — otherwise every "hi" renders a directory of fields."""
    reg = SkillRegistry()
    general = ("change-safety", "write-tests")
    for name in general:
        reg.register(load_skill_from_dir(_skill(tmp_path, name)))
    for name, trig in (
        ("godot-4", r"\bgodot\b"),
        ("bioinformatics", r"\b(fastq|rna-?seq)\b"),
        ("clinical-research", r"\b(randomised|randomized) (controlled )?trial\b"),
    ):
        reg.register(load_skill_from_dir(_skill(tmp_path, name, triggers=[trig])))

    def catalog(query: str, limit: int = 10) -> list[str]:
        return [s.manifest.name for s, _ in reg.match_skills(query, limit=limit)]

    def is_domain(name: str) -> bool:
        return bool(reg.get(name).manifest.triggers)

    # Unrelated / empty asks: every general procedure outranks every field pack,
    # so the packs fall off first when slots are scarce.
    for q in ("", "fix the login bug", "what's for dinner"):
        ranked = catalog(q)
        ranks = [is_domain(n) for n in ranked]
        assert ranks == sorted(ranks), f"{q!r}: a field pack outranked a general one ({ranked})"
        assert set(catalog(q, limit=len(general))) == set(general), q

    # The pack whose domain it is ranks first; its neighbours stay out.
    assert catalog("port the godot player controller")[0] == "godot-4"
    assert catalog("align these fastq reads")[0] == "bioinformatics"
    assert catalog("design a randomised controlled trial")[0] == "clinical-research"
    top2 = catalog("port the godot player controller", limit=2)
    assert [n for n in top2 if is_domain(n)] == ["godot-4"]
