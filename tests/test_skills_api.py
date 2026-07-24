"""HTTP /api/skills surface."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.models import Skill, SkillManifest, SkillStatus
from remedy.skills.registry import SkillRegistry


class _FakeRuntime:
    def __init__(self, tmp_path: Path):
        self.skills = SkillRegistry()
        self.config = type("C", (), {"home_dir": str(tmp_path)})()
        self.memory = None
        s = Skill(
            manifest=SkillManifest(
                name="api-skill",
                description="via api",
                status=SkillStatus.ACTIVE,
                tags=["api"],
            ),
            instructions="# body\n",
        )
        self.skills.register(s)


def test_list_skills(tmp_path: Path):
    rt = _FakeRuntime(tmp_path)
    app = create_app(runtime=rt, api_key="")
    client = TestClient(app)
    r = client.get("/api/skills")
    assert r.status_code == 200
    data = r.json()
    assert any(s["name"] == "api-skill" for s in data)


def test_skill_status(tmp_path: Path):
    rt = _FakeRuntime(tmp_path)
    app = create_app(runtime=rt, api_key="")
    client = TestClient(app)
    r = client.post(
        "/api/skills/api-skill/status",
        json={"status": "disabled"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "disabled"
    assert rt.skills.get("api-skill").manifest.status == SkillStatus.DISABLED


def test_skill_detail_404(tmp_path: Path):
    rt = _FakeRuntime(tmp_path)
    app = create_app(runtime=rt, api_key="")
    client = TestClient(app)
    r = client.get("/api/skills/missing-skill")
    assert r.status_code == 404


def test_skills_learning_summary(tmp_path: Path):
    rt = _FakeRuntime(tmp_path)
    learned = Skill(
        manifest=SkillManifest(
            name="auto-from-trace",
            description="Learned on the job",
            status=SkillStatus.DISCOVERED,
            metadata={
                "auto_generated": True,
                "lifecycle": "probation",
                "creation_gate": "Trace accepted; skill enters probation",
            },
        ),
        instructions="# how\n" + ("step\n" * 8),
    )
    rt.skills.register(learned)
    app = create_app(runtime=rt, api_key="")
    client = TestClient(app)
    r = client.get("/api/skills/learning/summary")
    assert r.status_code == 200
    data = r.json()
    assert data["learned_count"] >= 1
    assert data["probation_count"] >= 1
    assert any(s["name"] == "auto-from-trace" for s in data["recent"])
    # Must not be captured as /api/skills/{name}
    assert "note" in data
