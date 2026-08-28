"""On-PC job + git_status speed — filesystem and git probes off the hot path."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from remedy.core import agent_ship_tools as S
from remedy.core.jobs import run_explore_job
from tests.test_ship_tools import RT


class _RT:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.config = SimpleNamespace(home_dir=str(root))
        self.tool_registry = SimpleNamespace(tools={})

    def effective_project_path(self) -> Path:
        return self.root

    def resolve_tool_path(self, path: str, **_kw: Any) -> Path:
        raw = (path or ".").strip() or "."
        p = Path(raw)
        if not p.is_absolute():
            p = self.root / raw
        return p

    def allowed_roots(self) -> list[Path]:
        return [self.root]

    def access_scope(self) -> str:
        return "project"


@pytest.mark.asyncio
async def test_explore_job_lists_the_tree_off_the_loop(tmp_path: Path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# hi\n", encoding="utf-8")
    rt = _RT(tmp_path)
    result = await run_explore_job(rt, path=".")
    assert result.ok
    assert "Listing:" in result.summary
    assert "app.py" in result.summary or "src" in result.summary


@pytest.mark.asyncio
async def test_git_status_still_answers_when_not_a_repo(tmp_path: Path):
    rt = RT(tmp_path)
    S.register_ship_tools(rt)
    out = await rt.tool_registry.tools["git_status"]()
    assert "git_status" in out.lower()
