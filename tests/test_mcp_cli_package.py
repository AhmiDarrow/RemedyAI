"""MCP host packaging: entry points, CLI, and skill exposure."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

from remedy.tools.mcp_server import RemedyMCPServer
from remedy.tools.mcp_server import main as mcp_main


def test_mcp_entry_point_module_callable():
    assert callable(mcp_main)
    # main with no stdin interaction returns 0 when stdin is empty (EOF)
    # run_stdio_server exits loop on EOF
    code = subprocess.run(
        [sys.executable, "-c", "from remedy.tools.mcp_server import main; raise SystemExit(main([]))"],
        input=b"",
        capture_output=True,
        timeout=10,
        check=False,
    )
    assert code.returncode == 0


def test_mcp_stdio_roundtrip_initialize():
    """Pipe initialize + tools/list through the real stdio server process."""
    reqs = (
        json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            }
        )
        + "\n"
        + json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
        + "\n"
    )
    proc = subprocess.run(
        [
            sys.executable,
            "-c",
            "from remedy.tools.mcp_server import run_stdio_server; raise SystemExit(run_stdio_server())",
        ],
        input=reqs.encode("utf-8"),
        capture_output=True,
        timeout=30,
        check=False,
    )
    assert proc.returncode == 0, proc.stderr.decode("utf-8", errors="replace")
    lines = [ln for ln in proc.stdout.decode("utf-8").splitlines() if ln.strip()]
    assert len(lines) >= 2
    init = json.loads(lines[0])
    assert init["result"]["serverInfo"]["name"] == "remedy"
    listed = json.loads(lines[1])
    names = {t["name"] for t in listed["result"]["tools"]}
    assert "remedy_skill_list" in names
    assert "remedy_skill_get" in names


def test_mcp_lists_github_bundled_skill():
    srv = RemedyMCPServer()
    out = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "remedy_skill_list", "arguments": {"limit": 100}},
        }
    )
    text = out["result"]["content"][0]["text"]
    assert "github" in text.lower()


def test_mcp_skill_get_github():
    srv = RemedyMCPServer()
    out = srv.handle(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "remedy_skill_get", "arguments": {"name": "github"}},
        }
    )
    text = out["result"]["content"][0]["text"]
    assert "gh " in text or "GitHub" in text
    assert "quarantine" not in text.lower() or "Trust" not in text  # not blocked


def test_cli_mcp_help_registered():
    from remedy.interfaces.cli import build_parser

    p = build_parser()
    # remedy mcp serve
    ns = p.parse_args(["mcp", "serve"])
    assert ns.command == "mcp"
    assert ns.mcp_cmd == "serve"


def test_pyproject_declares_remedy_mcp_script():
    root = Path(__file__).resolve().parents[1]
    text = (root / "pyproject.toml").read_text(encoding="utf-8")
    assert "remedy-mcp" in text
    assert "remedy.tools.mcp_server:main" in text


def test_mcp_skill_run_rejects_jailbreak(tmp_path: Path, monkeypatch):
    from remedy.models import Skill, SkillManifest, SkillStatus
    from remedy.skills.registry import SkillRegistry

    monkeypatch.setenv("REMEDY_MCP_ALLOW_RUN", "1")
    skill_dir = tmp_path / "jail-skill"
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "scripts" / "ok.py").write_text("print('ok')\n", encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("---\nname: jail-skill\n---\n", encoding="utf-8")

    reg = SkillRegistry()
    reg.register(
        Skill(
            manifest=SkillManifest(
                name="jail-skill",
                description="jail test",
                status=SkillStatus.ACTIVE,
                path=str(skill_dir / "SKILL.md"),
            ),
            instructions="# jail",
            scripts=["scripts/ok.py"],
            source_skill_dir=str(skill_dir),
        )
    )
    srv = RemedyMCPServer()
    srv._reg = reg

    for bad in (r"C:\evil.py", r"..\evil.py", "scripts/../../evil.py"):
        out = srv.handle(
            {
                "jsonrpc": "2.0",
                "id": 9,
                "method": "tools/call",
                "params": {
                    "name": "remedy_skill_run",
                    "arguments": {"name": "jail-skill", "script": bad},
                },
            }
        )
        text = out["result"]["content"][0]["text"]
        assert "escapes" in text.lower() or "not found" in text.lower()
        assert "pwn" not in text.lower()
