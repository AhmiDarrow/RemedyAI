"""Session project_path HTTP is Go-owned; keep runtime/store guards.

Production create/update/list/bulk-project live in ``native/go/httpapi``.
The FastAPI twin is gone. Keep BasicRuntime / MemoryStore project jail tests.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from remedy.core.agent import BasicRuntime
from remedy.memory.store import MemoryStore
from remedy.models import AgentConfig, ChatSession


@pytest.fixture
def store(tmp_path: Path):
    s = MemoryStore(tmp_path / "sess_proj.db")
    asyncio.run(s.initialize())
    yield s
    asyncio.run(s.close())



def test_set_project_path_refuses_forbidden(tmp_path):
    from remedy.core.errors import SecurityError

    cfg = AgentConfig(
        name="t",
        project_path=str(tmp_path),
        access_scope="project",
        llm_provider="openai",
        llm_model="m",
        llm_api_key="k",
    )
    rt = BasicRuntime(cfg)
    rt.set_project_path(str(tmp_path), as_default=True)
    with pytest.raises(SecurityError):
        rt.set_project_path(r"C:\Windows", as_default=False)
    assert "Windows" not in str(rt.effective_project_path())


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


@pytest.mark.asyncio
async def test_forbidden_leftover_session_is_not_full(store: MemoryStore, tmp_path: Path):
    """Leftover C:\\Windows must not become access_scope=full."""
    from remedy.core.errors import SecurityError

    home = tmp_path / "remedy-home"
    home.mkdir()
    safe = tmp_path / "safe"
    safe.mkdir()
    sess = ChatSession(title="poison", project_path=r"C:\Windows")
    sess = await store.create_chat_session(sess)
    cfg = AgentConfig(
        name="t",
        project_path=str(safe),
        access_scope="project",
        llm_provider="openai",
        llm_model="m",
        llm_api_key="k",
        home_dir=str(home),
    )
    rt = BasicRuntime(cfg, memory=store)
    with pytest.raises(SecurityError):
        await rt._apply_session_workspace(sess.id)
    assert rt.access_scope() != "full"
    assert "Windows" not in str(rt.effective_project_path())
    cleared = await store.get_chat_session(sess.id)
    assert cleared is not None
    jail = str((home / "refused-project").resolve())
    stored = str(cleared.project_path or "")
    assert stored
    assert stored.replace("\\", "/").rstrip("/") == jail.replace("\\", "/").rstrip("/")
    await rt._apply_session_workspace(sess.id)
    assert rt.access_scope() != "full"
    assert "Windows" not in str(rt.effective_project_path())
