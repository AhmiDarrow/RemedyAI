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
