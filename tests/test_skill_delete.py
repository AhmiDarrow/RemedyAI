"""DELETE /api/skills/{name} — user skills only, not bundled."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from remedy.interfaces.routes.memory import register_memory_routes
from remedy.models import Skill, SkillKind, SkillManifest, SkillStatus


def _make_skill(name: str, path: Path, *, quarantine: bool = False) -> Skill:
    return Skill(
        manifest=SkillManifest(
            name=name,
            description=f"test {name}",
            kind=SkillKind.NATIVE,
            status=SkillStatus.DISCOVERED,
            path=str(path),
            metadata={"quarantine": quarantine, "source": "library"},
        ),
        instructions="do the thing",
        source_skill_dir=str(path),
    )


class _Reg:
    def __init__(self, skills: list[Skill]):
        self._by_name = {s.manifest.name: s for s in skills}

    def get(self, name: str):
        return self._by_name.get(name)

    def remove(self, name: str) -> bool:
        return self._by_name.pop(name, None) is not None

    @property
    def skills(self):
        return list(self._by_name.values())


@pytest.fixture()
def client(tmp_path: Path):
    skill_dir = tmp_path / "skills" / "temp-library-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# temp\n", encoding="utf-8")
    skill = _make_skill("temp-library-skill", skill_dir)
    reg = _Reg([skill])
    runtime = SimpleNamespace(
        skills=reg,
        config=SimpleNamespace(home_dir=str(tmp_path)),
    )
    app = FastAPI()
    register_memory_routes(app, runtime=runtime)
    return TestClient(app), skill_dir, reg, tmp_path


def test_delete_user_skill_removes_files(client):
    tc, skill_dir, reg, _home = client
    assert skill_dir.is_dir()
    r = tc.delete("/api/skills/temp-library-skill")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "deleted"
    assert body["removed_files"] is True
    assert not skill_dir.exists()
    assert reg.get("temp-library-skill") is None


def test_delete_missing_404(client):
    tc, *_ = client
    r = tc.delete("/api/skills/does-not-exist")
    assert r.status_code == 404


def test_delete_bundled_refused(tmp_path: Path):
    from remedy.bundled_skills import bundled_skills_dir

    bundled = bundled_skills_dir()
    # Use a real bundled folder if present; otherwise fake path under package tree
    candidates = [p for p in bundled.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()]
    if not candidates:
        pytest.skip("no bundled skills on disk")
    bpath = candidates[0]
    skill = _make_skill(bpath.name, bpath)
    reg = _Reg([skill])
    runtime = SimpleNamespace(
        skills=reg,
        config=SimpleNamespace(home_dir=str(tmp_path)),
    )
    app = FastAPI()
    register_memory_routes(app, runtime=runtime)
    tc = TestClient(app)
    r = tc.delete(f"/api/skills/{bpath.name}")
    assert r.status_code == 400
    assert "bundled" in r.json()["detail"].lower() or "cannot delete" in r.json()["detail"].lower()
    assert bpath.is_dir()  # untouched
