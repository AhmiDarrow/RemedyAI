"""Phase B2 checkpoints, B3 skill re-use metrics, Phase C MCP host."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remedy.core.checkpoint import (
    CheckpointStore,
    build_checkpoint_from_tool_steps,
)
from remedy.core.learning.refiner import SkillRefiner
from remedy.interfaces.api import create_app
from remedy.tools.mcp_server import RemedyMCPServer


def test_build_checkpoint_from_steps():
    steps = [
        {"tool": "file_read", "success": True, "result": "ok cfg"},
        {"tool": "bash_exec", "success": False, "error": "perm denied"},
        {"tool": "file_write", "success": True, "result": "wrote"},
    ]
    cp = build_checkpoint_from_tool_steps(
        steps, session_id="s1", title="Fix deploy", reason="auto"
    )
    assert cp.tool_step_count == 3
    assert any("file_read" in d for d in cp.done)
    assert any("bash_exec" in f for f in cp.failures)
    assert "bash_exec" in cp.tools_used
    md = cp.summary_markdown()
    assert "Done" in md
    assert "Next" in md


def test_checkpoint_store(tmp_path: Path):
    store = CheckpointStore(tmp_path)
    cp = build_checkpoint_from_tool_steps(
        [{"tool": "a", "success": True, "result": "1"}],
        session_id="sess-a",
        reason="manual",
    )
    store.save(cp)
    loaded = store.get(cp.id)
    assert loaded is not None
    assert loaded.session_id == "sess-a"
    latest = store.latest("sess-a")
    assert latest is not None
    assert latest.id == cp.id


def test_skill_activation_reuse_metrics(tmp_path: Path):
    path = tmp_path / "skill_stats.json"
    ref = SkillRefiner(stats_path=path)
    ref.record_execution("my-skill", True, session_id="s1")
    ref.record_activation("my-skill", session_id="s1")
    ref.record_activation("my-skill", session_id="s2")
    m = ref.get_reuse_metrics()
    assert m["total_activations"] == 2
    assert m["skills_with_activation"] == 1
    assert m["multi_session_reactivations"] == 1
    row = next(s for s in m["skills"] if s["name"] == "my-skill")
    assert row["activations"] == 2
    # reload durable
    ref2 = SkillRefiner(stats_path=path)
    assert ref2.get_stats("my-skill").activations == 2


def test_reuse_metrics_api(tmp_path: Path):
    class Cfg:
        home_dir = str(tmp_path)

    class RT:
        config = Cfg()
        skills = None

    # seed stats
    (tmp_path / "skills").mkdir()
    ref = SkillRefiner(stats_path=tmp_path / "skill_stats.json")
    ref.record_activation("demo", session_id="a")

    app = create_app(runtime=RT(), api_key="")
    client = TestClient(app)
    r = client.get("/api/skills/metrics/reuse")
    assert r.status_code == 200
    data = r.json()
    assert data["total_activations"] >= 1


def test_mcp_initialize_and_tools_list():
    srv = RemedyMCPServer()
    init = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "test", "version": "0"},
            },
        }
    )
    assert init is not None
    assert init["result"]["serverInfo"]["name"] == "remedy"
    assert "tools" in init["result"]["capabilities"]

    listed = srv.handle({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    names = {t["name"] for t in listed["result"]["tools"]}
    assert "remedy_skill_list" in names
    assert "remedy_skill_get" in names
    assert "remedy_plan_list" in names


def test_mcp_skill_list_and_quarantine_get(tmp_path: Path, monkeypatch):
    from remedy.models import Skill, SkillManifest, SkillStatus
    from remedy.skills.registry import SkillRegistry

    reg = SkillRegistry()
    reg.register(
        Skill(
            manifest=SkillManifest(
                name="safe-skill",
                description="A safe bundled-like skill",
                status=SkillStatus.ACTIVE,
            ),
            instructions="# do safe things\n" + ("step\n" * 5),
        )
    )
    reg.register(
        Skill(
            manifest=SkillManifest(
                name="bad-pack",
                description="Imported untrusted",
                status=SkillStatus.DISCOVERED,
                metadata={"quarantine": True},
            ),
            instructions="# evil\n",
        )
    )
    srv = RemedyMCPServer()
    srv._reg = reg

    listed = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "remedy_skill_list", "arguments": {}},
        }
    )
    text = listed["result"]["content"][0]["text"]
    assert "safe-skill" in text

    got = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "remedy_skill_get", "arguments": {"name": "bad-pack"}},
        }
    )
    body = got["result"]["content"][0]["text"]
    assert "quarantined" in body.lower()

    ok = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "remedy_skill_get", "arguments": {"name": "safe-skill"}},
        }
    )
    assert "do safe things" in ok["result"]["content"][0]["text"]


def test_mcp_skill_run_requires_opt_in(monkeypatch):
    srv = RemedyMCPServer()
    monkeypatch.delenv("REMEDY_MCP_ALLOW_RUN", raising=False)
    # Ensure registry loads without crash
    out = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {
                "name": "remedy_skill_run",
                "arguments": {"name": "anything"},
            },
        }
    )
    assert "REMEDY_MCP_ALLOW_RUN" in out["result"]["content"][0]["text"]


def test_mcp_notification_no_response():
    srv = RemedyMCPServer()
    resp = srv.handle(
        {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    )
    assert resp is None
