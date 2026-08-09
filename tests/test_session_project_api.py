"""Session project_path create/update/list — sidebar tree contract."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from remedy.core.agent import BasicRuntime
from remedy.interfaces.api import create_app
from remedy.memory.store import MemoryStore
from remedy.models import AgentConfig, ChatSession


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "sess_proj.db")
    asyncio.run(s.initialize())
    yield s
    asyncio.run(s.close())


@pytest.fixture
def client(store: MemoryStore, tmp_path: Path, monkeypatch):
    monkeypatch.setenv("REMEDY_API_AUTH", "0")
    cfg = AgentConfig(
        name="test",
        project_path="",  # unset global project
        llm_provider="openai",
        llm_model="test",
        llm_api_key="x",
        llm_base_url="http://127.0.0.1:9/v1",
    )
    runtime = BasicRuntime(cfg, memory=store)
    app = create_app(runtime=runtime, memory=store, api_key="")
    with TestClient(app) as c:
        yield c, tmp_path


def test_create_explicit_no_project_does_not_inherit(client):
    c, _ = client
    r = c.post("/api/sessions", json={"title": "Loose", "project_path": ""})
    assert r.status_code == 200
    body = r.json()
    assert body.get("project_path") in (None, "", ".")


def test_create_empty_project_stays_root_even_with_global_default(
    store: MemoryStore, tmp_path: Path, monkeypatch
):
    """Desktop New Session sends project_path='' — must not inherit config default."""
    monkeypatch.setenv("REMEDY_API_AUTH", "0")
    proj = tmp_path / "GlobalDefault"
    proj.mkdir()
    raw_cfg = {
        "project_path": str(proj),
        "home_dir": str(tmp_path / ".remedy"),
        "access_scope": "project",
    }
    (tmp_path / ".remedy").mkdir(exist_ok=True)
    # Modularized: load_config is used from sessions.crud (not the package root).
    monkeypatch.setattr(
        "remedy.interfaces.routes.sessions.crud.load_config",
        lambda: dict(raw_cfg),
    )
    # Legacy / shared import path used by other session helpers
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: dict(raw_cfg),
    )
    cfg = AgentConfig(
        name="test",
        project_path=str(proj),
        llm_provider="openai",
        llm_model="test",
        llm_api_key="x",
        llm_base_url="http://127.0.0.1:9/v1",
    )
    runtime = BasicRuntime(cfg, memory=store)
    app = create_app(runtime=runtime, memory=store, api_key="")
    with TestClient(app) as c:
        r = c.post("/api/sessions", json={"title": "Root", "project_path": ""})
        assert r.status_code == 200
        assert r.json().get("project_path") in (None, "", ".")

        # Omitting field inherits global config project_path
        r2 = c.post("/api/sessions", json={"title": "Inherit?"})
        assert r2.status_code == 200
        inherited = r2.json().get("project_path") or ""
        assert "GlobalDefault" in str(inherited).replace("/", "\\")


def test_create_with_project_path(client):
    c, tmp = client
    proj = tmp / "MyApp"
    proj.mkdir()
    r = c.post(
        "/api/sessions",
        json={"title": "In app", "project_path": str(proj)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["project_path"]
    assert "MyApp" in body["project_path"].replace("/", "\\") or "MyApp" in body[
        "project_path"
    ]


def test_patch_move_and_clear_project(client):
    c, tmp = client
    proj = tmp / "Work"
    proj.mkdir()
    r = c.post("/api/sessions", json={"title": "Move me", "project_path": ""})
    sid = r.json()["id"]

    r2 = c.patch(f"/api/sessions/{sid}", json={"project_path": str(proj)})
    assert r2.status_code == 200
    assert r2.json()["project_path"]
    assert "Work" in str(r2.json()["project_path"])

    r3 = c.patch(f"/api/sessions/{sid}", json={"project_path": ""})
    assert r3.status_code == 200
    assert r3.json().get("project_path") in (None, "", ".")


def test_list_sessions_includes_project_path(client):
    c, tmp = client
    proj = tmp / "Listed"
    proj.mkdir()
    c.post("/api/sessions", json={"title": "A", "project_path": str(proj)})
    c.post("/api/sessions", json={"title": "B", "project_path": ""})
    r = c.get("/api/sessions?limit=50")
    assert r.status_code == 200
    sessions = r.json()["sessions"]
    assert len(sessions) >= 2
    paths = {s.get("title"): s.get("project_path") for s in sessions}
    assert paths.get("A")
    assert paths.get("B") in (None, "", ".")


@pytest.mark.asyncio
async def test_store_clear_project_path(store: MemoryStore, tmp_path: Path):
    proj = tmp_path / "p"
    proj.mkdir()
    s = ChatSession(title="t", project_path=str(proj))
    saved = await store.create_chat_session(s)
    assert saved.project_path

    cleared = await store.update_chat_session(saved.id, project_path=None)
    assert cleared is not None
    assert cleared.project_path is None


def test_agent_unset_project_forces_full_access(tmp_path: Path):
    cfg = AgentConfig(
        name="t",
        project_path="",
        access_scope="project",
        llm_provider="openai",
        llm_model="m",
        llm_api_key="k",
    )
    rt = BasicRuntime(cfg)
    assert rt.project_path_is_unset() is True
    assert rt.access_scope() == "full"

    rt.set_project_path(str(tmp_path / "code"), as_default=True)
    (tmp_path / "code").mkdir(exist_ok=True)
    assert rt.project_path_is_unset() is False
    assert rt.access_scope() == "project"


@pytest.mark.asyncio
async def test_apply_session_workspace_binds_project(store: MemoryStore, tmp_path: Path):
    """Streaming turn must jail tools to the session project, not leftover state."""
    proj_a = tmp_path / "A"
    proj_b = tmp_path / "B"
    proj_a.mkdir()
    proj_b.mkdir()
    sa = ChatSession(title="a", project_path=str(proj_a))
    sb = ChatSession(title="b", project_path=str(proj_b))
    snone = ChatSession(title="none", project_path=None)
    sa = await store.create_chat_session(sa)
    sb = await store.create_chat_session(sb)
    snone = await store.create_chat_session(snone)

    cfg = AgentConfig(
        name="t",
        project_path=str(proj_a),
        access_scope="project",
        llm_provider="openai",
        llm_model="m",
        llm_api_key="k",
    )
    rt = BasicRuntime(cfg, memory=store)

    await rt._apply_session_workspace(sb.id)
    assert "B" in str(rt.effective_project_path())
    assert rt.project_path_is_unset() is False

    await rt._apply_session_workspace(snone.id)
    assert rt.project_path_is_unset() is True
    assert rt.access_scope() == "full"


def test_bulk_set_session_project(client):
    c, tmp = client
    proj = tmp / "Bulk"
    proj.mkdir()
    ids = []
    for title in ("x", "y", "z"):
        r = c.post("/api/sessions", json={"title": title, "project_path": ""})
        ids.append(r.json()["id"])
    r = c.post(
        "/api/sessions/bulk-project",
        json={"session_ids": ids, "project_path": str(proj)},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 3
    assert "Bulk" in str(body["project_path"])
    listed = c.get("/api/sessions?limit=50").json()["sessions"]
    by_id = {s["id"]: s for s in listed}
    for sid in ids:
        assert by_id[sid].get("project_path")
        assert "Bulk" in str(by_id[sid]["project_path"])
