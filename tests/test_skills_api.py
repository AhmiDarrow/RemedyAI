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


def test_force_promote_and_quarantine(tmp_path: Path):
    rt = _FakeRuntime(tmp_path)
    learned = Skill(
        manifest=SkillManifest(
            name="early-skill",
            description="probation",
            status=SkillStatus.DISCOVERED,
            metadata={"auto_generated": True},
        ),
        instructions="# steps\n1. do thing\n",
    )
    rt.skills.register(learned)
    app = create_app(runtime=rt, api_key="")
    client = TestClient(app)
    r = client.post(
        "/api/skills/early-skill/status",
        json={"status": "active", "force_promote": True},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "active"
    assert r.json().get("quarantine") is False

    r2 = client.post(
        "/api/skills/early-skill/quarantine",
        json={"quarantine": True},
    )
    assert r2.status_code == 200
    assert r2.json()["quarantine"] is True
    assert rt.skills.get("early-skill").manifest.metadata.get("quarantine") is True


def test_skill_body_put(tmp_path: Path):
    rt = _FakeRuntime(tmp_path)
    app = create_app(runtime=rt, api_key="")
    client = TestClient(app)
    r = client.put(
        "/api/skills/api-skill/body",
        json={"instructions": "# Updated by human\n\nDo the thing carefully.\n"},
    )
    assert r.status_code == 200
    assert r.json()["status"] == "saved"
    assert "Updated by human" in (rt.skills.get("api-skill").instructions or "")
