"""F1 / owner's manual always readable by the agent."""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.help_docs import (
    clear_help_docs_cache,
    help_read_roots,
    list_help_articles,
    read_help_article,
    resolve_help_article,
)
from remedy.core.workspace import allowed_roots_for_scope


@pytest.fixture(autouse=True)
def _clear_cache(monkeypatch):
    clear_help_docs_cache()
    # Prefer this repo so tests find docs/manual even without REMEDY_DEV_ROOT
    root = Path(__file__).resolve().parents[1]
    monkeypatch.setenv("REMEDY_DEV_ROOT", str(root))
    clear_help_docs_cache()
    yield
    clear_help_docs_cache()


def test_help_roots_discover_manual():
    roots = help_read_roots()
    assert roots
    assert any((r / "computer-use-soak.md").is_file() for r in roots) or any(
        (r / "00-overview.md").is_file() for r in roots
    )


def test_list_and_read_soak_notes():
    arts = list_help_articles()
    ids = {a["id"] for a in arts}
    assert "computer-use-soak" in ids
    assert "00-overview" in ids
    assert "19-metabolism" in ids

    path = resolve_help_article("computer-use-soak")
    assert path is not None and path.is_file()

    body = read_help_article("computer-use-soak")
    assert body["ok"] is True
    assert "soak" in (body.get("content") or "").lower()
    assert "computer" in (body.get("title") or "").lower() or "soak" in (
        body.get("title") or ""
    ).lower()


def test_help_read_unknown_lists_hint():
    body = read_help_article("not-a-real-article-zzz")
    assert body["ok"] is False
    assert "help_list" in (body.get("error") or "").lower() or "Unknown" in (
        body.get("error") or ""
    )


def test_allowed_roots_include_help_even_when_project_elsewhere(tmp_path: Path):
    """Project = unrelated folder; help manuals still on read roots."""
    proj = tmp_path / "OtherProject"
    proj.mkdir()
    roots = allowed_roots_for_scope("project", proj)
    # At least one help root with a known article
    from remedy.core.help_docs import resolve_help_article

    soak = resolve_help_article("computer-use-soak")
    assert soak is not None
    ok = False
    for r in roots:
        try:
            soak.resolve().relative_to(r.resolve())
            ok = True
            break
        except ValueError:
            continue
    assert ok, f"soak path {soak} not under read roots {roots}"


@pytest.mark.asyncio
async def test_help_tools_registered_and_readable():
    from types import SimpleNamespace

    from remedy.core.agent_workspace_tools import register_workspace_tools
    from remedy.skills.tool_registry import ToolRegistry

    reg = ToolRegistry()
    rt = SimpleNamespace(
        tool_registry=reg,
        effective_project_path=lambda: Path.cwd(),
        access_scope=lambda: "project",
        allowed_roots=lambda: [],
        write_roots=lambda: [],
        resolve_tool_path=lambda p, **k: Path(p),
        _register_comfyui_tools=lambda: None,
        _register_vision_tools=lambda: None,
        _register_local_discover_tools=lambda: None,
        _register_skill_tools=lambda: None,
    )
    register_workspace_tools(rt)
    names = {t.name for t in reg.tools}
    assert "help_list" in names
    assert "help_read" in names

    listing = await reg.execute("help_list")
    assert "computer-use-soak" in listing
    assert "help_read" in listing.lower() or "F1" in listing

    body = await reg.execute("help_read", id="computer-use-soak")
    assert "soak" in body.lower()
    assert "checklist" in body.lower() or "precondition" in body.lower()
