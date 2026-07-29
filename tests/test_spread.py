"""Tests for silent spread (fan-out) planner + runner + jail/approval integration."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from remedy.core.react_policy import tool_content_is_error
from remedy.core.spread.planner import plan_spread
from remedy.core.spread.runner import run_spread
from remedy.core.spread.types import SpreadTask


def test_plan_spread_chat_no():
    p = plan_spread("hello there", intent="chat", use_local=False)
    assert p.spread is False


def test_plan_spread_multi_area_yes():
    p = plan_spread(
        "Review the auth module and the database layer and the API routes in parallel",
        intent="tool",
        use_local=False,
    )
    assert p.spread is True
    assert len(p.tasks) >= 2
    assert "spread_run" in p.system_hint() or "Spread" in p.system_hint()


def test_plan_spread_single_file_no():
    p = plan_spread(
        "Only edit this file foo.py to fix the typo",
        intent="tool",
        use_local=False,
    )
    assert p.spread is False


def test_plan_spread_inside_worker_no():
    p = plan_spread(
        "Review auth and api across the codebase",
        intent="tool",
        inside_worker=True,
        use_local=False,
    )
    assert p.spread is False
    assert p.reason == "inside_worker"


def test_tool_content_is_error_approval():
    assert tool_content_is_error("APPROVAL_REQUIRED id=abc\nreason=ask")
    assert tool_content_is_error("[0] path.py: APPROVAL_REQUIRED id=x reason=y")
    # Buried in spread digest header (not only prefix)
    buried = (
        "[spread_run ok=True workers=2 wall_ms=12]\n"
        "## [t1] explore (ok)\nlisting\n\n"
        "## [v1] verify (fail)\nAPPROVAL_REQUIRED id=abc\nreason=ask\n"
    )
    assert tool_content_is_error(buried)
    assert not tool_content_is_error("Wrote 12 lines to path.py")


def test_snapshot_spread_never_uses_local():
    """Hot-path planner must not block on local SmolVLM2 (use_local=False)."""
    from remedy.core.context_snapshot import build_context_snapshot

    snap = build_context_snapshot(
        messages=[{"role": "user", "content": "hi"}],
        user_text="Review auth and database and api modules in parallel across the codebase",
        session_id="perf-test",
    )
    sp = (snap.signals or {}).get("spread") or {}
    # Heuristic may say spread; method must not be local_model from snapshot
    if sp.get("spread"):
        assert sp.get("method") != "local_model"


@pytest.mark.asyncio
async def test_run_spread_parallel_wall_time(tmp_path: Path):
    """Three delayed workers should finish near max delay, not sum."""

    async def slow_job(runtime, kind, **kwargs):
        from remedy.core.jobs import JobResult

        await asyncio.sleep(0.15)
        return JobResult(kind=kind, ok=True, summary=f"ok-{kind}-{kwargs.get('path')}")

    runtime = MagicMock()
    runtime.effective_project_path.return_value = tmp_path
    runtime.resolve_tool_path.side_effect = lambda p: tmp_path / (p if p not in (".",) else "")
    runtime.allowed_roots.return_value = [tmp_path]
    runtime.access_scope.return_value = "project"

    tasks = [
        SpreadTask(id="a", kind="explore", path="a", goal="a"),
        SpreadTask(id="b", kind="explore", path="b", goal="b"),
        SpreadTask(id="c", kind="explore", path="c", goal="c"),
    ]

    import remedy.core.spread.runner as runner_mod

    original = runner_mod._job

    async def patched(runtime, kind, **kwargs):
        await asyncio.sleep(0.15)
        return f"ok-{kind}", True, {}

    runner_mod._job = patched  # type: ignore[assignment]
    try:
        t0 = time.perf_counter()
        result = await run_spread(runtime, tasks, max_workers=3, reason="test")
        wall = time.perf_counter() - t0
    finally:
        runner_mod._job = original  # type: ignore[assignment]

    assert result.ok
    assert len(result.results) == 3
    # Sequential would be ~0.45s; parallel ~0.15–0.30s
    assert wall < 0.40, f"expected parallel speedup, wall={wall:.3f}s"
    assert "ok-explore" in result.merged_summary


@pytest.mark.asyncio
async def test_run_spread_blocks_nesting():
    runtime = MagicMock()
    tasks = [SpreadTask(id="t1", kind="explore", path="."), SpreadTask(id="t2", kind="diff")]

    from remedy.core.spread import runner as runner_mod

    token = runner_mod._spread_depth.set(1)
    try:
        result = await run_spread(runtime, tasks, reason="nested")
        assert result.strategy == "skipped"
        assert result.reason == "recursive_spread_blocked"
    finally:
        runner_mod._spread_depth.reset(token)


def test_parse_tasks_arg_accepts_native_list():
    """Models pass tasks as a JSON array via tool_calls — must not .strip() a list."""
    from remedy.core.agent_spread_tools import _parse_tasks_arg

    items, err = _parse_tasks_arg(
        [
            {"id": "t1", "kind": "explore", "path": "src/a"},
            {"id": "t2", "kind": "explore", "path": "src/b"},
        ]
    )
    assert err is None
    assert items is not None and len(items) == 2

    items2, err2 = _parse_tasks_arg(
        '[{"id":"t1","kind":"diff","path":"."},{"id":"t2","kind":"explore","path":"tests"}]'
    )
    assert err2 is None
    assert items2 is not None and len(items2) == 2

    items3, err3 = _parse_tasks_arg({"kind": "explore", "path": "src"})
    assert err3 is None
    assert items3 is not None and len(items3) == 1

    empty, err_e = _parse_tasks_arg("")
    assert empty is None and err_e is None


@pytest.mark.asyncio
async def test_spread_run_tool_accepts_list_tasks(tmp_path: Path):
    """Regression: AttributeError 'list' object has no attribute 'strip'."""
    from remedy.core.agent_spread_tools import register_spread_tools
    from remedy.skills.tool_registry import ToolRegistry

    runtime = MagicMock()
    runtime.tool_registry = ToolRegistry()
    runtime.effective_project_path.return_value = tmp_path
    runtime.resolve_tool_path.side_effect = lambda p: tmp_path
    runtime.allowed_roots.return_value = [tmp_path]
    runtime.access_scope.return_value = "project"
    runtime._session_id = "spread-list-test"

    (tmp_path / "a.txt").write_text("hello\n", encoding="utf-8")

    register_spread_tools(runtime)
    assert runtime.tool_registry.get("spread_run") is not None

    # Native list (how OpenAI-compat tool_calls often arrive after json.loads)
    out = await runtime.tool_registry.execute(
        "spread_run",
        goal="",
        tasks=[
            {"id": "t1", "kind": "explore", "path": "."},
            {"id": "t2", "kind": "diff", "path": "."},
        ],
        max_workers=2,
        path=".",
    )
    assert isinstance(out, str)
    assert not out.startswith("Error")
    assert "spread" in out.lower() or "worker" in out.lower() or "Explore" in out


def test_jobs_resolve_fail_closed():
    from remedy.core.errors import SecurityError
    from remedy.core.jobs import _resolve_job_path

    runtime = MagicMock()

    def boom(path: str):
        raise SecurityError("Path outside allowed roots", rule="path_traversal")

    runtime.resolve_tool_path.side_effect = boom
    with pytest.raises(SecurityError):
        _resolve_job_path(runtime, "C:/Windows/System32")


def test_repo_search_absolute_outside_roots(tmp_path: Path):
    from remedy.core.repo_search import search_repo

    root = tmp_path / "proj"
    root.mkdir()
    (root / "a.py").write_text("hello\n", encoding="utf-8")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.py").write_text("secret_token_xyz\n", encoding="utf-8")

    hits, engine = search_repo(
        root,
        "secret_token_xyz",
        path=str(outside),
        allowed_roots=[root],
        access_scope="project",
        force_python=True,
    )
    assert hits == []
    assert "outside" in engine or "error" in engine


@pytest.mark.asyncio
async def test_verify_job_approval_gate(tmp_path: Path, monkeypatch):
    from remedy.core.approvals import APPROVALS
    from remedy.core.jobs import run_verify_job

    runtime = MagicMock()
    runtime.effective_project_path.return_value = tmp_path
    runtime.resolve_tool_path.side_effect = lambda p: tmp_path
    runtime.allowed_roots.return_value = [tmp_path]
    runtime._session_id = "sess-test"

    # needs_ask re-syncs from config — pin ask mode so desktop auto config cannot skip.
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "approval_mode": "ask"},
    )
    prev = APPROVALS.mode
    try:
        APPROVALS.set_mode("ask")
        result = await run_verify_job(
            runtime, command="echo hello-spread-test-unique", path="."
        )
        assert result.ok is False
        assert "APPROVAL_REQUIRED" in result.summary
    finally:
        APPROVALS.set_mode(prev)
