"""Phase B1: structured task plans + plan-mode tool allowlist."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remedy.core.plan_store import (
    PLAN_MODE_SYSTEM_ADDENDUM,
    PLAN_MODE_TOOL_NAMES,
    PlanStore,
    parse_steps_from_text,
)
from remedy.interfaces.api import create_app
from remedy.models import ToolCall


def test_parse_steps_from_text():
    text = """
    Here is the plan:
    1. Research the API
    2. Write tests
    - Deploy carefully
    """
    steps = parse_steps_from_text(text)
    assert "Research the API" in steps
    assert "Write tests" in steps
    assert "Deploy carefully" in steps


def test_plan_store_roundtrip(tmp_path: Path):
    store = PlanStore(tmp_path)
    plan = store.create(
        "Ship uninstall fix",
        goal="Clean wipe on reinstall",
        steps=["Inventory paths", "Align NSIS wipe", "Add tests"],
        risks=["OneDrive home relocation"],
        session_id="sess-1",
        status="draft",
    )
    assert plan.id
    loaded = store.get(plan.id)
    assert loaded is not None
    assert loaded.title == "Ship uninstall fix"
    assert len(loaded.steps) == 3
    assert loaded.steps[0].title == "Inventory paths"

    approved = store.set_status(plan.id, "approved")
    assert approved is not None
    assert approved.status == "approved"

    latest = store.latest_for_session("sess-1")
    assert latest is not None
    assert latest.id == plan.id
    md = latest.summary_markdown()
    assert "Inventory paths" in md
    assert "Build" in md


def test_latest_for_session_does_not_leak_other_session(tmp_path: Path):
    """Fresh session must not inherit another chat's plan (Plan banner bug)."""
    store = PlanStore(tmp_path)
    store.create(
        "Old session plan",
        goal="Belong to A",
        steps=["Step A"],
        session_id="sess-A",
    )
    store.create(
        "Untagged plan",
        goal="No session",
        steps=["Orphan"],
        session_id=None,
    )
    assert store.latest_for_session("sess-B") is None
    assert store.latest_for_session("sess-A") is not None
    assert store.latest_for_session("sess-A").title == "Old session plan"
    # Strict filter: untagged plans do not match a session id
    listed = store.list_plans(session_id="sess-B", limit=10)
    assert listed == []


def test_plan_mode_tool_names_exclude_shell():
    assert "plan_save" in PLAN_MODE_TOOL_NAMES
    assert "bash_exec" not in PLAN_MODE_TOOL_NAMES
    assert "file_write" not in PLAN_MODE_TOOL_NAMES
    assert "file_edit" not in PLAN_MODE_TOOL_NAMES
    # Research tools allowed in plan mode
    assert "file_read" in PLAN_MODE_TOOL_NAMES
    assert "list_dir" in PLAN_MODE_TOOL_NAMES
    assert "repo_search" in PLAN_MODE_TOOL_NAMES
    assert "web_search" in PLAN_MODE_TOOL_NAMES
    assert "media_read" in PLAN_MODE_TOOL_NAMES
    assert "vision_describe" in PLAN_MODE_TOOL_NAMES
    # High-impact / mutating tools must stay out
    for blocked in (
        "skill_run",
        "job_run",
        "mission_start",
        "comfyui",
        "apply_patch",
    ):
        assert blocked not in PLAN_MODE_TOOL_NAMES
    assert "Approve" in PLAN_MODE_SYSTEM_ADDENDUM or "plan_save" in PLAN_MODE_SYSTEM_ADDENDUM


def test_plans_api(tmp_path: Path, monkeypatch):
    # Point config home at tmp via fake runtime.config
    class Cfg:
        home_dir = str(tmp_path)

    class RT:
        config = Cfg()
        def list_tasks(self):
            return []
        def create_task(self, *a, **k):
            raise NotImplementedError

    app = create_app(runtime=RT(), api_key="")
    client = TestClient(app)
    r = client.post(
        "/api/plans",
        json={
            "title": "Add plan mode",
            "goal": "Make Plan real",
            "steps": ["Wire API", "Restrict tools", "Show in UI"],
            "risks": ["Too many tools still available"],
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert data["plan"]["title"] == "Add plan mode"
    assert len(data["plan"]["steps"]) == 3
    pid = data["plan"]["id"]

    r2 = client.get("/api/plans")
    assert r2.status_code == 200
    assert any(p["id"] == pid for p in r2.json()["plans"])

    r3 = client.post(f"/api/plans/{pid}/status", json={"status": "approved"})
    assert r3.status_code == 200
    assert r3.json()["plan"]["status"] == "approved"

    r4 = client.get("/api/plans/latest")
    assert r4.status_code == 200
    assert r4.json()["plan"]["id"] == pid

    # With a session_id that has no plans, do NOT fall back to global latest
    r5 = client.get("/api/plans/latest", params={"session_id": "fresh-empty-session"})
    assert r5.status_code == 200
    assert r5.json()["plan"] is None


def test_call_tool_blocks_in_plan_mode():
    """BasicRuntime.call_tool refuses non-plan tools when _plan_mode is set."""
    import asyncio

    from remedy.core.agent import BasicRuntime
    from remedy.models import AgentConfig

    rt = BasicRuntime(AgentConfig(name="t", llm_api_key="x"))
    rt._plan_mode = True

    async def _run():
        return await rt.call_tool(
            ToolCall(tool_name="bash_exec", arguments={"command": "echo hi"})
        )

    res = asyncio.run(_run())
    assert res.success is False
    assert "Plan mode" in (res.error or "") or "PLAN_MODE" in (res.error or "")
