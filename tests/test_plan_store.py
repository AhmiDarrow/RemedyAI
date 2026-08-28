"""Phase B1: structured task plans + plan-mode tool allowlist."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remedy.core.plan_store import (
    PLAN_MODE_SYSTEM_ADDENDUM,
    PLAN_MODE_TOOL_NAMES,
    PlanStep,
    PlanStore,
    normalize_block_reason,
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


def test_create_joins_list_title_and_goal(tmp_path: Path):
    """Models send JSON arrays for title/goal — must not .strip() a list."""
    store = PlanStore(tmp_path)
    plan = store.create(
        ["Ship it"],
        goal=["do the thing"],
        steps=["Inventory"],
        session_id="s-list",
    )
    assert plan.title == "Ship it"
    assert plan.goal == "do the thing"
    loaded = store.get(plan.id)
    assert loaded is not None
    assert loaded.title == "Ship it"
    assert loaded.goal == "do the thing"
    untitled = store.create([], goal="", session_id="s-empty-title")
    assert untitled.title == "Untitled plan"
    untitled2 = store.create("   ", goal=["  "], session_id="s-ws")
    assert untitled2.title == "Untitled plan"
    assert untitled2.goal == ""


def test_create_normalizes_done_with_pending_steps(tmp_path: Path):
    """Agent must not stick the banner on done while all steps are still pending."""
    store = PlanStore(tmp_path)
    plan = store.create(
        "Premature done",
        steps=["A", "B"],
        session_id="s1",
        status="done",
    )
    assert plan.status == "draft"
    assert all(s.status == "pending" for s in plan.steps)


def test_create_supersedes_previous_actionable(tmp_path: Path):
    store = PlanStore(tmp_path)
    old = store.create("V1", steps=["a"], session_id="s1", status="draft")
    new = store.create("V2", steps=["b"], session_id="s1", status="draft")
    reloaded_old = store.get(old.id)
    assert reloaded_old is not None
    assert reloaded_old.status == "cancelled"
    assert new.status == "draft"
    latest = store.latest_for_session("s1", actionable_only=True)
    assert latest is not None
    assert latest.id == new.id


def test_latest_actionable_skips_terminal(tmp_path: Path):
    store = PlanStore(tmp_path)
    store.create("Done plan", steps=["x"], session_id="s1", status="draft")
    done = store.list_plans(session_id="s1", limit=1)[0]
    store.set_status(done.id, "done")
    assert store.latest_for_session("s1") is not None
    assert store.latest_for_session("s1").status == "done"
    assert store.latest_for_session("s1", actionable_only=True) is None


def test_plan_mode_tool_names_exclude_shell():
    assert "plan_save" in PLAN_MODE_TOOL_NAMES
    assert "plan_step_status" in PLAN_MODE_TOOL_NAMES
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
    # F1 manuals always readable in Plan (same as Build)
    assert "help_list" in PLAN_MODE_TOOL_NAMES
    assert "help_read" in PLAN_MODE_TOOL_NAMES
    # High-impact / mutating tools must stay out
    for blocked in (
        "skill_run",
        "job_run",
        "mission_start",
        "comfyui",
        "apply_patch",
        "computer_click",
        "computer_type",
    ):
        assert blocked not in PLAN_MODE_TOOL_NAMES
    assert "Approve" in PLAN_MODE_SYSTEM_ADDENDUM or "plan_save" in PLAN_MODE_SYSTEM_ADDENDUM
    assert "Build" in PLAN_MODE_SYSTEM_ADDENDUM or "Ctrl+B" in PLAN_MODE_SYSTEM_ADDENDUM


def test_update_step_status_by_id_and_index(tmp_path: Path):
    from remedy.core.plan_store import BUILD_MODE_SYSTEM_ADDENDUM

    store = PlanStore(tmp_path)
    plan = store.create(
        "Build app",
        steps=["Scaffold", "Crypto", "UI"],
        session_id="s-steps",
        status="approved",
    )
    u = store.update_step_status(plan.id, "s1", "done")
    assert u is not None
    assert u.steps[0].status == "done"
    assert u.status == "active"  # auto-promote from approved
    u2 = store.update_step_status(plan.id, "2", "active")
    assert u2 is not None
    assert u2.steps[1].status == "active"
    # Cosmetic [done] title match + strip
    plan2 = store.create(
        "Hack titles",
        steps=[{"id": "s1", "title": "[done] Already", "status": "pending"}],
        session_id="s-hack",
        status="active",
    )
    u3 = store.update_step_status(plan2.id, "Already", "done")
    assert u3 is not None
    assert u3.steps[0].status == "done"
    assert not u3.steps[0].title.lower().startswith("[done]")
    # All done → plan done
    store.update_step_status(plan.id, "s2", "done")
    u4 = store.update_step_status(plan.id, "s3", "done")
    assert u4 is not None
    assert u4.status == "done"
    assert "file_edit" in BUILD_MODE_SYSTEM_ADDENDUM
    assert "plan_step_status" in BUILD_MODE_SYSTEM_ADDENDUM
    from remedy.core.plan_store import FRONTIER_BUILD_MODE_ADDENDUM

    assert "plan_step_status" in FRONTIER_BUILD_MODE_ADDENDUM
    assert "file_edit" in FRONTIER_BUILD_MODE_ADDENDUM
    assert "7400" in FRONTIER_BUILD_MODE_ADDENDUM
    assert "1. **Explore" not in FRONTIER_BUILD_MODE_ADDENDUM


def test_old_plan_json_loads_without_evidence_fields(tmp_path: Path):
    step = PlanStep.from_dict({"id": "s1", "title": "Click Submit", "status": "pending"})
    assert step.intended == ""
    assert step.observed == ""
    assert step.evidence == ""
    assert step.block_reason == ""
    store = PlanStore(tmp_path)
    plan = store.create("Legacy", steps=[{"id": "s1", "title": "Click Submit"}], session_id="s")
    loaded = store.get(plan.id)
    assert loaded is not None
    assert loaded.steps[0].observed == ""
    done = store.update_step_status(plan.id, "s1", "done")
    assert done is not None
    assert done.steps[0].status == "done"
    assert done.steps[0].observed == ""


def test_step_evidence_and_block_reason_roundtrip(tmp_path: Path):
    store = PlanStore(tmp_path)
    plan = store.create("Order groceries", steps=["Add usual items"], session_id="s-ev")
    u = store.update_step_status(
        plan.id,
        "s1",
        "active",
        intended="Cart has the 12 usual items",
        observed="URL still on the product page",
        evidence="snapshot after Add to cart",
        block_reason="couldnt_verify",
    )
    assert u is not None
    assert u.steps[0].intended.startswith("Cart has")
    assert "product page" in u.steps[0].observed
    assert u.steps[0].block_reason == "couldnt_verify"
    md = u.summary_markdown()
    assert "observed:" in md
    assert "blocked: couldnt_verify" in md
    again = store.get(plan.id)
    assert again is not None
    assert again.steps[0].block_reason == "couldnt_verify"
    assert normalize_block_reason("verification_failure") == "couldnt_verify"
    assert normalize_block_reason("not-a-reason") == ""
    skipped = store.update_step_status(plan.id, "s1", "skipped")
    assert skipped is not None
    assert skipped.steps[0].block_reason == "couldnt_verify"  # keep explicit reason
    plan2 = store.create("Skip me", steps=["Optional"], session_id="s-sk")
    sk = store.update_step_status(plan2.id, "s1", "skipped")
    assert sk is not None
    assert sk.steps[0].block_reason == "skipped"


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

    r4 = client.post(
        f"/api/plans/{pid}/steps/status",
        json={"step_id": "s1", "status": "done"},
    )
    assert r4.status_code == 200
    body = r4.json()["plan"]
    assert body["steps"][0]["status"] == "done"
    assert body["status"] == "active"

    r4 = client.get("/api/plans/latest")
    assert r4.status_code == 200
    assert r4.json()["plan"]["id"] == pid

    # With a session_id that has no plans, do NOT fall back to global latest
    r5 = client.get("/api/plans/latest", params={"session_id": "fresh-empty-session"})
    assert r5.status_code == 200
    assert r5.json()["plan"] is None

    r6 = client.post(f"/api/plans/{pid}/status", json={"status": "cancelled"})
    assert r6.status_code == 200
    assert r6.json()["plan"]["status"] == "cancelled"

    r7 = client.get("/api/plans/latest", params={"actionable": "true"})
    assert r7.status_code == 200
    # Cancelled plan must not surface as actionable latest
    assert r7.json()["plan"] is None


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


def test_plan_save_joins_list_title_and_goal(tmp_path: Path):
    """plan_save(title=["Ship it"]) stores prose, not a Python repr."""
    import asyncio

    from remedy.core.agent import BasicRuntime
    from remedy.core.plan_store import PlanStore
    from remedy.models import AgentConfig

    home = tmp_path / "home"
    home.mkdir()
    rt = BasicRuntime(AgentConfig(name="t", llm_api_key="x", home_dir=str(home)))
    rt._session_id = "sess-list-title"

    async def _run():
        return await rt.call_tool(
            ToolCall(
                tool_name="plan_save",
                arguments={
                    "title": ["Ship it"],
                    "goal": ["do the thing"],
                    "steps": ["Write tests"],
                    "status": "draft",
                },
            )
        )

    res = asyncio.run(_run())
    assert res.success is True, res.error or res.data
    body = str(res.data or res.error or "")
    assert "Ship it" in body
    assert "['Ship it']" not in body
    assert "do the thing" in body
    store = PlanStore(home)
    latest = store.latest_for_session("sess-list-title")
    assert latest is not None
    assert latest.title == "Ship it"
    assert latest.goal == "do the thing"


def test_plan_save_accepts_native_step_arrays(tmp_path: Path):
    """Models pass steps/risks as JSON arrays via tool_calls — must not .strip() a list."""
    import asyncio

    from remedy.core.agent import BasicRuntime
    from remedy.models import AgentConfig

    home = tmp_path / "home"
    home.mkdir()
    rt = BasicRuntime(AgentConfig(name="t", llm_api_key="x", home_dir=str(home)))
    rt._session_id = "sess-native-plan"

    async def _run():
        return await rt.call_tool(
            ToolCall(
                tool_name="plan_save",
                arguments={
                    "title": "Ship native steps",
                    "goal": "Accept arrays",
                    "steps": [
                        {"title": "Parse arrays", "detail": "like spread_run"},
                        "Write tests",
                        {"id": "s3", "title": "Commit"},
                    ],
                    "risks": ["regression", {"title": "schema drift"}],
                    "status": "draft",
                },
            )
        )

    res = asyncio.run(_run())
    assert res.success is True, res.error or res.data
    body = str(res.data or res.error or "")
    assert "Plan saved" in body
    assert "Parse arrays" in body
    assert "Write tests" in body
    assert "Commit" in body
    assert "regression" in body
    assert "schema drift" in body
    # Must not crash / claim zero steps
    assert "steps=3" in body or "3." in body


def test_plan_show_blocks_cross_session_plan_id(tmp_path: Path):
    """plan_show(plan_id=) must not leak another session's plan body."""
    import asyncio

    from remedy.core.agent import BasicRuntime
    from remedy.core.plan_store import PlanStore
    from remedy.models import AgentConfig

    home = tmp_path / "home"
    home.mkdir()
    store = PlanStore(home)
    other = store.create(
        "Secret other plan",
        goal="private",
        steps=["Do not leak"],
        session_id="sess-A",
        status="draft",
    )

    rt = BasicRuntime(AgentConfig(name="t", llm_api_key="x", home_dir=str(home)))
    rt._session_id = "sess-B"

    async def _run():
        return await rt.call_tool(
            ToolCall(tool_name="plan_show", arguments={"plan_id": other.id})
        )

    res = asyncio.run(_run())
    assert res.success is True
    body = str(res.data or res.error or "")
    assert "Do not leak" not in body
    assert "another session" in body.lower() or "cross-session" in body.lower()
