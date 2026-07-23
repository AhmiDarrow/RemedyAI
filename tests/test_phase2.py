"""Tests for Phase 2: skill executor, validator, exporter, tool registry, and deep adapters."""

from pathlib import Path

import pytest

from remedy.models import Skill, SkillManifest, SkillStatus, ToolDefinition, ToolSource
from remedy.skills.adapters.hermes_deep import (
    batch_migrate_hermes,
    deep_load_hermes_skill,
    map_hermes_tools,
    parse_hermes_config,
)
from remedy.skills.adapters.openclaw_deep import (
    deep_load_openclaw_skill,
    extract_channel_config,
    extract_mcp_servers,
    parse_clawhub_manifest,
    register_mcp_tools_from_skill,
)
from remedy.skills.executor import SkillExecutor
from remedy.skills.exporter import SkillExporter
from remedy.skills.tool_registry import ToolRegistry
from remedy.skills.validator import SkillValidator


class TestSkillExecutor:
    def test_extract_code_blocks(self):
        executor = SkillExecutor()
        text = """Some text
```python
print("hello")
```
More text
```bash
echo test
```
"""
        blocks = executor._extract_code_blocks(text)
        assert len(blocks) == 2
        assert blocks[0] == ("python", 'print("hello")')
        assert blocks[1] == ("bash", "echo test")

    def test_extract_no_blocks(self):
        executor = SkillExecutor()
        blocks = executor._extract_code_blocks("plain text")
        assert blocks == []

    @pytest.mark.asyncio
    async def test_run_python_block(self, tmp_path):
        executor = SkillExecutor(sandbox_dir=tmp_path)
        result = await executor._run_python_block('print("hello world")')
        assert result.success
        assert "hello world" in result.stdout

    @pytest.mark.asyncio
    async def test_run_failing_script(self, tmp_path):
        executor = SkillExecutor(sandbox_dir=tmp_path)
        result = await executor._run_python_block('import sys; sys.exit(1)')
        assert not result.success
        assert result.exit_code == 1

    @pytest.mark.asyncio
    async def test_run_script_async(self, tmp_path):
        script = tmp_path / "test_script.py"
        script.write_text('print("async test")')
        executor = SkillExecutor(sandbox_dir=tmp_path)
        result = await executor.run_script(script)
        assert result.success
        assert "async test" in result.stdout

    @pytest.mark.asyncio
    async def test_run_script_not_found(self, tmp_path):
        executor = SkillExecutor(sandbox_dir=tmp_path)
        result = await executor.run_script(Path("nonexistent.py"))
        assert not result.success
        assert result.exit_code != 0

    @pytest.mark.asyncio
    async def test_run_all_scripts(self, tmp_path):
        (tmp_path / "scripts").mkdir()
        (tmp_path / "scripts" / "a.py").write_text('print("a")')
        (tmp_path / "scripts" / "b.py").write_text('print("b")')
        executor = SkillExecutor(sandbox_dir=tmp_path)
        results = await executor.run_all_scripts(["scripts/a.py", "scripts/b.py"], tmp_path)
        assert len(results) == 2
        assert results["scripts/a.py"].success
        assert results["scripts/b.py"].success

    @pytest.mark.asyncio
    async def test_run_instructions(self, tmp_path):
        executor = SkillExecutor(sandbox_dir=tmp_path)
        results = await executor.run_instructions(
            '```python\nprint("ok")\n```\n```bash\necho hi\n```'
        )
        assert len(results) == 2
        assert results[0].success


class TestSkillValidator:
    def test_validate_metadata_complete(self):
        skill = Skill(
            manifest=SkillManifest(
                name="test-skill",
                description="A skill with a long enough description",
                version="1.0.0",
                author="Tester",
                tags=["test"],
                status=SkillStatus.ACTIVE,
            ),
            instructions="This is a skill with sufficient instruction length for validation",
        )
        validator = SkillValidator()
        result = validator.validate_metadata(skill)
        assert result.is_valid
        assert len(result.errors) == 0

    def test_validate_metadata_incomplete(self):
        skill = Skill(
            manifest=SkillManifest(
                name="",
                description="short",
                version="",
            ),
            instructions="short",
        )
        validator = SkillValidator()
        result = validator.validate_metadata(skill)
        assert not result.is_valid
        assert len(result.errors) >= 1

    def test_validate_metadata_invalid_version(self):
        skill = Skill(
            manifest=SkillManifest(
                name="test",
                description="A skill with a long enough description",
                version="not-a-version",
            ),
            instructions="Some instructions that are long enough",
        )
        validator = SkillValidator()
        result = validator.validate_metadata(skill)
        assert result.is_valid
        assert len(result.warnings) >= 1

    def test_validate_scripts(self):
        skill = Skill(
            manifest=SkillManifest(
                name="test",
                description="A skill with a long enough description",
                version="1.0.0",
                path="/tmp/test_skill",
            ),
            scripts=["nonexistent.py"],
            source_skill_dir="/tmp/test_skill",
        )
        validator = SkillValidator()
        result = validator.validate_scripts(skill)
        assert not result.is_valid

    def test_compute_score(self):
        from remedy.skills.validator import ValidationResult
        r1 = ValidationResult("a")
        r2 = ValidationResult("b")
        r2.add_error("broken")

        validator = SkillValidator()
        score = validator.compute_score([r1, r2])
        assert score == 0.5


class TestSkillExporter:
    def test_export_native(self, tmp_path):
        skill = Skill(
            manifest=SkillManifest(
                name="export-test",
                description="A test skill for export",
                version="1.0.0",
                author="Tester",
                tags=["test"],
            ),
            instructions="# Export Test\n\nSome instructions.",
            scripts=[],
            references=[],
        )
        exporter = SkillExporter(tmp_path / "out")
        dest = exporter.export_native(skill)
        assert dest.is_dir()
        assert (dest / "SKILL.md").is_file()
        content = (dest / "SKILL.md").read_text()
        assert "export-test" in content
        assert "Export Test" in content

    def test_export_hermes(self, tmp_path):
        skill = Skill(
            manifest=SkillManifest(
                name="hermes-export",
                description="Hermes export test",
                version="1.0.0",
                tags=["hermes"],
            ),
            instructions="Hermes format instructions.",
        )
        exporter = SkillExporter(tmp_path / "out")
        dest = exporter.export_hermes(skill)
        assert dest.is_dir()
        assert (dest / "SKILL.md").is_file()
        content = (dest / "SKILL.md").read_text()
        assert "hermes-export" in content

    def test_export_openclaw(self, tmp_path):
        skill = Skill(
            manifest=SkillManifest(
                name="oc-export",
                description="OpenClaw export test",
                version="1.0.0",
            ),
            instructions="OpenClaw instructions.",
        )
        exporter = SkillExporter(tmp_path / "out")
        dest = exporter.export_openclaw(skill)
        assert dest.is_dir()
        assert (dest / "skill.yaml").is_file()

    def test_export_zip(self, tmp_path):
        out_dir = tmp_path / "out"
        out_dir.mkdir()
        skill = Skill(
            manifest=SkillManifest(
                name="zip-export",
                description="ZIP export test",
                version="1.0.0",
            ),
            instructions="ZIP content.",
        )
        exporter = SkillExporter(out_dir)
        zip_path = exporter.export_zip(skill)
        assert zip_path.is_file()
        assert zip_path.suffix == ".zip"


class TestToolRegistry:
    def test_register_builtin(self):
        registry = ToolRegistry()
        t = registry.register_builtin("test_tool", "A test tool")
        assert t.name == "test_tool"
        assert t.source == ToolSource.BUILTIN
        assert registry.tool_count == 1

    def test_register_from_mcp(self):
        registry = ToolRegistry()
        t = registry.register_from_mcp("my-server", {
            "name": "mcp_tool",
            "description": "MCP tool",
            "parameters": {"type": "object"},
        })
        assert t.source == ToolSource.MCP
        assert t.uri == "mcp://my-server/mcp_tool"
        assert registry.tool_count == 1

    def test_register_duplicate_overwrites(self):
        registry = ToolRegistry()
        registry.register_builtin("dupe", "First")
        registry.register_builtin("dupe", "Second")
        assert registry.tool_count == 1
        assert registry.get("dupe").description == "Second"

    def test_get_by_source(self):
        registry = ToolRegistry()
        registry.register_builtin("bt", "builtin")
        registry.register(ToolDefinition(name="st", description="skill", source=ToolSource.SKILL))
        assert registry.get("bt", ToolSource.BUILTIN) is not None
        assert registry.get("bt", ToolSource.SKILL) is None

    def test_list_by_source(self):
        registry = ToolRegistry()
        registry.register_builtin("a", "a")
        registry.register_builtin("b", "b")
        registry.register(ToolDefinition(name="c", description="c", source=ToolSource.MCP))
        builtins = registry.list_by_source(ToolSource.BUILTIN)
        assert len(builtins) == 2

    def test_search(self):
        registry = ToolRegistry()
        registry.register_builtin("memory_search", "Search memory")
        registry.register_builtin("file_read", "Read files")
        results = registry.search("memory")
        assert len(results) == 1
        results = registry.search("nonexistent")
        assert len(results) == 0

    def test_get_stats_empty(self):
        registry = ToolRegistry()
        stats = registry.get_stats()
        assert stats["total_calls"] == 0
        assert stats["registered_tools"] == 0
        assert stats["success_rate"] == 0.0
        assert stats["by_source"] == {}


class TestHermesDeepAdapter:
    def test_map_hermes_tools(self):
        mapped = map_hermes_tools(["memory_search", "unknown_tool"])
        assert "remedy_memory_search" in mapped
        assert "unknown_tool" in mapped

    def test_deep_load_hermes_skill(self, tmp_path):
        skill_dir = tmp_path / "hermes-skill"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: deep-test
description: Deep load test
version: 2.0.0
hermes_version: 2.5
tools:
  - memory_search
  - web_fetch
---
# Deep skill
""")
        skill = deep_load_hermes_skill(skill_dir)
        assert skill.manifest.name == "deep-test"
        assert skill.manifest.metadata["origin"] == "hermes"
        assert "remedy_memory_search" in skill.manifest.tools

    def test_deep_load_with_config(self, tmp_path):
        skill_dir = tmp_path / "hermes-skill-2"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: config-test
description: Config test
version: 1.0.0
---
# Config skill
""")
        config_path = tmp_path / "hermes_config.yaml"
        config_path.write_text("tools:\n  memory:\n    - memory_search\n")
        skill = deep_load_hermes_skill(skill_dir, config_path=config_path)
        assert skill.manifest.name == "config-test"

    def test_parse_hermes_config(self, tmp_path):
        config = tmp_path / "hc.yaml"
        config.write_text("name: hermes\ntools:\n  search: [mem_search]\n")
        parsed = parse_hermes_config(config)
        assert parsed["name"] == "hermes"

    def test_parse_hermes_config_missing(self, tmp_path):
        parsed = parse_hermes_config(tmp_path / "nonexistent.yaml")
        assert parsed == {}

    def test_batch_migrate(self, tmp_path):
        base = tmp_path / "hermes-skills"
        base.mkdir()
        for i in range(3):
            sd = base / f"skill-{i}"
            sd.mkdir()
            (sd / "SKILL.md").write_text(f"""---
name: skill-{i}
description: Skill {i}
version: 1.0.0
---
# Skill {i}
""")
        count, skills, errors = batch_migrate_hermes(base)
        assert count == 3
        assert len(errors) == 0


class TestOpenClawDeepAdapter:
    def test_parse_clawhub_manifest(self, tmp_path):
        path = tmp_path / "skill.yaml"
        import yaml
        path.write_text(yaml.dump({"name": "oc-skill", "description": "An OC skill"}))
        manifest = parse_clawhub_manifest(path)
        assert manifest["name"] == "oc-skill"

    def test_extract_mcp_servers(self):
        manifest = {
            "mcp": {
                "search-server": {
                    "command": "search",
                    "args": [],
                    "tools": ["web_search"],
                }
            }
        }
        servers = extract_mcp_servers(manifest)
        assert len(servers) == 1
        assert servers[0]["name"] == "search-server"

    def test_extract_mcp_servers_empty(self):
        assert extract_mcp_servers({}) == []

    def test_extract_channel_config(self):
        manifest = {
            "channels": {
                "telegram": {"token": "xxx"},
                "discord": {"token": "yyy"},
            }
        }
        channels = extract_channel_config(manifest)
        assert len(channels) == 2
        assert "telegram" in channels

    def test_deep_load_openclaw_skill_md(self, tmp_path):
        skill_dir = tmp_path / "oc-deep"
        skill_dir.mkdir()
        (skill_dir / "SKILL.md").write_text("""---
name: oc-deep-test
description: Deep OC test
version: 1.0.0
tools:
  - oc_search
---
# Deep OC skill
""")
        skill = deep_load_openclaw_skill(skill_dir)
        assert skill.manifest.name == "oc-deep-test"
        assert skill.manifest.metadata["origin"] == "openclaw"

    def test_deep_load_openclaw_skill_yaml(self, tmp_path):
        import yaml
        skill_dir = tmp_path / "oc-yaml"
        skill_dir.mkdir()
        (skill_dir / "skill.yaml").write_text(yaml.dump({
            "name": "oc-yaml-test",
            "description": "YAML-based OC skill",
            "mcp": {"srv": {"tools": ["t1", "t2"]}},
            "channels": {"telegram": {"token": "abc"}},
        }))
        skill = deep_load_openclaw_skill(skill_dir)
        assert skill.manifest.name == "oc-yaml-test"
        assert "mcp_servers" in skill.manifest.metadata
        assert "channels" in skill.manifest.metadata

    def test_register_mcp_tools_from_skill(self):
        skill = Skill(
            manifest=SkillManifest(
                name="mcp-test",
                description="MCP test skill",
                version="1.0.0",
                metadata={
                    "mcp_servers": [{"name": "s1", "tools": ["t1", "t2"]}],
                },
            ),
            instructions="",
        )
        registry = ToolRegistry()
        count = register_mcp_tools_from_skill(registry, skill)
        assert count == 2
        assert registry.tool_count == 2
