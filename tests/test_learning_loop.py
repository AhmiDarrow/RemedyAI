"""Tests for the learning loop."""

import pytest

from remedy.core.learning_loop import LearningLoop
from remedy.models import Task


@pytest.fixture
def ll(tmp_path):
    skills_dir = tmp_path / "skills"
    return LearningLoop(skills_dir=skills_dir, memory=None)


class TestLearningLoop:
    def test_detect_too_short_traces(self, ll):
        task = Task(title="Simple thing")
        trace = [{"tool": "echo", "result": "ok"}]
        result = ll.generate_skill_candidate(task, trace)
        assert result is None

    def test_generate_from_longer_trace(self, ll):
        task = Task(title="Build a thing step by step")
        trace = [
            {"tool": "read_file", "result": "found config"},
            {"tool": "edit_file", "result": "modified"},
            {"tool": "run_tests", "result": "all passed"},
        ]
        content = ll.generate_skill_candidate(task, trace)
        assert content is not None
        assert "build-a-thing-step-by-step" in content
        assert "read_file" in content
        assert "all passed" in content

    def test_generate_with_proposed_name(self, ll):
        task = Task(title="Do the dance")
        trace = [
            {"tool": "step1", "result": "ok"},
            {"tool": "step2", "result": "ok"},
            {"tool": "step3", "result": "ok"},
        ]
        content = ll.generate_skill_candidate(task, trace, proposed_name="dance-skill")
        assert "dance-skill" in content
        assert "Do the dance" in content  # description still references task title

    def test_save_candidate(self, ll):
        content = "---\nname: saved-skill\ndescription: test\n---\n\n# Test"
        path = ll.save_candidate(content, "Saved Skill")
        assert path.exists()
        assert path.name == "SKILL.md"
        assert "saved-skill" in path.read_text()
        assert path.parent.name == "saved-skill"

    def test_slugify(self, ll):
        assert ll._slugify("Hello World! @#$") == "hello-world"
        assert ll._slugify("  Spaces  everywhere  ") == "spaces-everywhere"
        assert ll._slugify("___") == "unnamed-skill"

    def test_extract_tools(self, ll):
        trace = [
            {"tool": "read_file", "result": "..."},
            {"tool": "edit_file", "result": "..."},
            {"tool": "read_file", "result": "..."},  # duplicate
        ]
        tools = ll._extract_tools(trace)
        assert tools == {"read_file", "edit_file"}

    def test_extract_steps(self, ll):
        trace = [
            {"description": "Read config", "result": "found"},
            {"tool": "write", "result": "saved"},
        ]
        steps = ll._extract_steps(trace)
        assert len(steps) == 2
        assert "Read config" in steps[0]
        assert "write" in steps[1]
