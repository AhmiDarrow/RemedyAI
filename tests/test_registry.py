"""Tests for the skill registry."""

import pytest

from remedy.models import Skill, SkillKind, SkillManifest, SkillStatus
from remedy.skills.registry import SkillRegistry


def make_skill(name: str, kind: SkillKind = SkillKind.NATIVE) -> Skill:
    return Skill(
        manifest=SkillManifest(
            name=name,
            description=f"Skill {name}",
            kind=kind,
        )
    )


class TestSkillRegistry:
    def test_register_and_count(self):
        reg = SkillRegistry()
        reg.register(make_skill("s1"))
        reg.register(make_skill("s2"))
        assert reg.count == 2

    def test_register_overwrites_same_name(self):
        reg = SkillRegistry()
        s1 = make_skill("unique")
        s2 = make_skill("unique")
        reg.register(s1)
        reg.register(s2)
        assert reg.count == 1

    def test_get_by_name(self):
        reg = SkillRegistry()
        s = make_skill("finder")
        reg.register(s)
        assert reg.get("finder") is not None
        assert reg.get("finder").id == s.id

    def test_get_by_uuid(self):
        reg = SkillRegistry()
        s = make_skill("uuid-test")
        reg.register(s)
        assert reg.get(s.id) is not None
        assert reg.get(s.id).manifest.name == "uuid-test"

    def test_get_nonexistent(self):
        reg = SkillRegistry()
        assert reg.get("missing") is None

    def test_activate_deactivate(self):
        reg = SkillRegistry()
        reg.register(make_skill("toggle"))
        assert reg.activate("toggle") is True
        assert reg.get("toggle").manifest.status == SkillStatus.ACTIVE
        assert reg.deactivate("toggle") is True
        assert reg.get("toggle").manifest.status == SkillStatus.DISABLED
        assert reg.activate("nope") is False

    def test_validate_all(self):
        reg = SkillRegistry()
        s1 = make_skill("v1")
        s2 = make_skill("v2")
        reg.register(s1)
        reg.register(s2)
        reg.activate("v1")
        reg.activate("v2")

        total, validated = reg.validate_all()
        assert total == 2
        assert validated == 2

    def test_search(self):
        reg = SkillRegistry()
        reg.register(
            Skill(
                manifest=SkillManifest(
                    name="http-client",
                    description="Makes HTTP requests",
                    tags=["networking", "api"],
                )
            )
        )
        reg.register(
            Skill(
                manifest=SkillManifest(
                    name="file-manager",
                    description="Manages files",
                    tags=["io"],
                )
            )
        )

        results = reg.search("http")
        assert len(results) == 1
        assert results[0].manifest.name == "http-client"

        results = reg.search("networking")
        assert len(results) == 1

    def test_remove(self):
        reg = SkillRegistry()
        reg.register(make_skill("removable"))
        assert reg.count == 1
        assert reg.remove("removable") is True
        assert reg.count == 0
        assert reg.remove("removable") is False

    def test_active_list(self):
        reg = SkillRegistry()
        reg.register(make_skill("a"))
        reg.register(make_skill("b"))
        reg.register(make_skill("c"))
        reg.activate("a")
        reg.activate("c")

        active = reg.active
        assert len(active) == 2

    def test_clear(self):
        reg = SkillRegistry()
        reg.register(make_skill("x"))
        reg.register(make_skill("y"))
        reg.clear()
        assert reg.count == 0
        assert reg.active == []

    def test_discover_from_directory(self, tmp_path):
        d = tmp_path / "skills"
        d.mkdir()
        for name in ["alpha", "beta"]:
            sd = d / name
            sd.mkdir()
            (sd / "SKILL.md").write_text(
                f"---\nname: {name}\ndescription: Skill {name}\n---\n\nBody"
            )

        reg = SkillRegistry()
        count = reg.discover(str(d))
        assert count == 2
        assert reg.get("alpha") is not None

    def test_load_single(self, tmp_path):
        sd = tmp_path / "single-skill"
        sd.mkdir()
        (sd / "SKILL.md").write_text(
            "---\nname: solo\ndescription: Alone\n---\n\n# Solo"
        )

        reg = SkillRegistry()
        skill = reg.load_single(str(sd))
        assert skill.manifest.name == "solo"
        assert reg.get("solo") is not None
