"""Tests for shared Pydantic models."""

import pytest
from remedy.models import (
    AgentConfig,
    HandoffNote,
    MemoryEntry,
    MemoryEntryType,
    Skill,
    SkillKind,
    SkillManifest,
    SkillStatus,
    Task,
    TaskStatus,
)


class TestSkillManifest:
    def test_minimal_manifest(self):
        m = SkillManifest(name="test-skill", description="A test skill")
        assert m.name == "test-skill"
        assert m.description == "A test skill"
        assert m.kind == SkillKind.NATIVE
        assert m.status == SkillStatus.DISCOVERED
        assert m.version == "1.0.0"
        assert m.tags == []

    def test_full_manifest(self):
        m = SkillManifest(
            name="full-skill",
            description="A full skill",
            version="2.0.0",
            author="Test Author",
            license="MIT",
            tags=["test", "example"],
            kind=SkillKind.HERMES,
            homepage="https://example.com",
            requires=["pydantic"],
            tools=["mcp-tool-1"],
        )
        assert m.version == "2.0.0"
        assert m.author == "Test Author"
        assert "test" in m.tags
        assert m.requires == ["pydantic"]
        assert m.tools == ["mcp-tool-1"]
        assert m.is_native is False


class TestSkill:
    def test_skill_with_manifest(self):
        manifest = SkillManifest(name="cool-skill", description="Does cool things")
        skill = Skill(manifest=manifest, instructions="# Do this\n\nThen that.")
        assert skill.manifest.name == "cool-skill"
        assert len(skill.instructions) > 0
        assert skill.id is not None
        assert skill.scripts == []


class TestMemoryEntry:
    def test_default_entry(self):
        e = MemoryEntry(title="Note", content="Some content")
        assert e.entry_type == MemoryEntryType.NOTE
        assert e.importance == 0.5
        assert e.tags == []

    def test_custom_entry(self):
        e = MemoryEntry(
            title="Bug found",
            content="The flux capacitor is misaligned",
            entry_type=MemoryEntryType.USER_FACT,
            importance=0.9,
            tags=["bug", "urgent"],
        )
        assert e.entry_type == MemoryEntryType.USER_FACT
        assert e.importance == 0.9
        assert "urgent" in e.tags


class TestHandoffNote:
    def test_handoff_creation(self):
        h = HandoffNote(
            title="Session handoff",
            content="Worked on Phase 0 scaffolding.",
            action_items=["Run tests", "Commit code"],
            decisions=["Use aiosqlite for memory"],
        )
        assert h.acknowledged is False
        assert len(h.action_items) == 2
        assert len(h.decisions) == 1


class TestTask:
    def test_task_lifecycle(self):
        t = Task(title="Build Remedy", description="Phase 0 scaffold")
        assert t.status == TaskStatus.CREATED
        assert t.sub_tasks == []

    def test_task_with_subtasks(self):
        t = Task(title="Parent task")
        t.sub_tasks = [Task(title="Child 1").id, Task(title="Child 2").id]
        assert len(t.sub_tasks) == 2


class TestAgentConfig:
    def test_default_config(self):
        c = AgentConfig()
        assert c.name == "Remedy"
        assert c.home_dir == "~/.remedy"
        assert c.sarcasm_mode is False
        assert c.allow_skill_creation is True
        assert c.auto_approve_threshold == 0.8

    def test_custom_config(self):
        c = AgentConfig(
            name="Reme",
            sarcasm_mode=True,
            enabled_channels=["telegram", "discord"],
        )
        assert c.name == "Reme"
        assert c.sarcasm_mode is True
        assert c.enabled_channels == ["telegram", "discord"]
