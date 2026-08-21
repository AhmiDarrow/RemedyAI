"""Remedy as an MCP *server* (stdio JSON-RPC) — personal partner Phase C.

Exposes this machine's skills (and read-only helpers) to external hosts
(any MCP-compatible client) over loopback/stdio only.

No multi-tenant gateway — same-owner local capability export.
"""

from __future__ import annotations

import contextlib
import json
import logging
import os
import sys
from pathlib import Path
from typing import Any

from remedy import __version__
from remedy.home import default_home

logger = logging.getLogger(__name__)

PROTOCOL_VERSION = "2024-11-05"


def _home() -> Path:
    env = os.environ.get("REMEDY_HOME")
    if env:
        return Path(env).expanduser()
    try:
        from remedy.interfaces.config import load_config

        h = load_config().get("home_dir")
        if h:
            return Path(h).expanduser()
    except Exception:
        pass
    return default_home()


def _load_registry():
    from remedy.skills.loader import discover_skills
    from remedy.skills.registry import SkillRegistry

    reg = SkillRegistry()
    home = _home()
    skills_dir = home / "skills"
    paths: list[Path] = []
    if skills_dir.is_dir():
        paths.append(skills_dir)
    # Bundled skills package
    try:
        import remedy.bundled_skills as bs

        bundled = Path(bs.__file__).resolve().parent
        if bundled.is_dir():
            paths.append(bundled)
    except Exception:
        pass
    for p in paths:
        try:
            for skill in discover_skills(p):
                reg.register(skill)
        except Exception as e:
            logger.debug("discover %s failed: %s", p, e)
    return reg


def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "name": "remedy_skill_list",
            "description": (
                "List Remedy skills available on this machine "
                "(name, status, description). Learned + bundled packs."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "include_disabled": {
                        "type": "boolean",
                        "description": "Include disabled/deprecated skills",
                        "default": False,
                    },
                    "limit": {"type": "integer", "default": 50},
                },
            },
        },
        {
            "name": "remedy_skill_search",
            "description": "Search Remedy skills by query (ranked catalog).",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "query": {"type": "string"},
                    "limit": {"type": "integer", "default": 10},
                },
                "required": ["query"],
            },
        },
        {
            "name": "remedy_skill_get",
            "description": (
                "Load full SKILL.md instructions for a Trusted skill. "
                "Quarantined packs are refused until the owner Trusts them in Remedy."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string", "description": "Skill name"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "remedy_skill_run",
            "description": (
                "Run a skill script (scripts/). Blocked for quarantined skills. "
                "Requires REMEDY_MCP_ALLOW_RUN=1 in the environment (owner opt-in)."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "script": {"type": "string", "description": "Script filename"},
                    "args": {"type": "string", "description": "Optional args string"},
                },
                "required": ["name"],
            },
        },
        {
            "name": "remedy_plan_list",
            "description": "List structured task plans saved by Remedy Plan mode.",
            "inputSchema": {
                "type": "object",
                "properties": {"limit": {"type": "integer", "default": 10}},
            },
        },
        {
            "name": "remedy_plan_show",
            "description": "Show a plan by id, or the latest plan.",
            "inputSchema": {
                "type": "object",
                "properties": {"plan_id": {"type": "string"}},
            },
        },
    ]


class RemedyMCPServer:
    """Minimal stdio MCP server (newline-delimited JSON-RPC 2.0)."""

    def __init__(self) -> None:
        self._reg = None
        self._initialized = False

    @property
    def registry(self):
        if self._reg is None:
            self._reg = _load_registry()
        return self._reg

    def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        """Handle one JSON-RPC message. Returns response or None for notifications."""
        if not isinstance(message, dict):
            return self._error(None, -32600, "Invalid Request")
        mid = message.get("id")
        method = message.get("method")
        params = message.get("params") or {}
        if not isinstance(params, dict):
            params = {}

        # Notifications (no id)
        if mid is None and method:
            if method == "notifications/initialized":
                self._initialized = True
            return None

        if method == "initialize":
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {
                    "protocolVersion": PROTOCOL_VERSION,
                    "capabilities": {"tools": {}},
                    "serverInfo": {
                        "name": "remedy",
                        "version": __version__,
                    },
                },
            }

        if method == "ping":
            return {"jsonrpc": "2.0", "id": mid, "result": {}}

        if method == "tools/list":
            return {
                "jsonrpc": "2.0",
                "id": mid,
                "result": {"tools": _tool_defs()},
            }

        if method == "tools/call":
            name = str(params.get("name") or "")
            arguments = params.get("arguments") or {}
            if not isinstance(arguments, dict):
                arguments = {}
            try:
                text = self._call_tool(name, arguments)
                return {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "content": [{"type": "text", "text": text}],
                        "isError": False,
                    },
                }
            except Exception as e:
                return {
                    "jsonrpc": "2.0",
                    "id": mid,
                    "result": {
                        "content": [{"type": "text", "text": f"Error: {e}"}],
                        "isError": True,
                    },
                }

        if method == "resources/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"resources": []}}

        if method == "prompts/list":
            return {"jsonrpc": "2.0", "id": mid, "result": {"prompts": []}}

        return self._error(mid, -32601, f"Method not found: {method}")

    def _call_tool(self, name: str, args: dict[str, Any]) -> str:
        reg = self.registry
        if name == "remedy_skill_list":
            include_disabled = bool(args.get("include_disabled", False))
            limit = max(1, min(int(args.get("limit") or 50), 200))
            skills = list(reg.skills)[: limit * 2]
            lines = []
            for s in skills:
                st = (
                    s.manifest.status.value
                    if hasattr(s.manifest.status, "value")
                    else str(s.manifest.status)
                )
                if not include_disabled and st in ("disabled", "deprecated"):
                    continue
                meta = s.manifest.metadata or {}
                q = " [quarantine]" if meta.get("quarantine") else ""
                lines.append(f"- {s.manifest.name} ({st}){q}: {s.manifest.description}")
                if len(lines) >= limit:
                    break
            return "Skills:\n" + ("\n".join(lines) if lines else "(none)")

        if name == "remedy_skill_search":
            q = str(args.get("query") or "").strip()
            limit = max(1, min(int(args.get("limit") or 10), 30))
            if not q:
                return "Provide query."
            ranked = reg.match_skills(q, limit=limit, include_disabled=False)
            if not ranked:
                return f"No skills matched: {q}"
            lines = [
                f"- {s.manifest.name} (score={sc:.2f}): {s.manifest.description}"
                for s, sc in ranked
            ]
            return "Matches:\n" + "\n".join(lines)

        if name == "remedy_skill_get":
            nm = str(args.get("name") or "").strip()
            if not nm:
                return "Provide name."
            skill = reg.get(nm)
            if skill is None:
                hits = reg.match_skills(nm, limit=5)
                hint = ", ".join(s.manifest.name for s, _ in hits) or "none"
                return f"Skill not found: {nm}. Closest: {hint}"
            meta = skill.manifest.metadata or {}
            if meta.get("quarantine"):
                return (
                    f"Skill '{nm}' is quarantined. Trust it in Remedy Desktop "
                    "(Skills panel) before external apps can read the full body."
                )
            body = skill.instructions or ""
            if hasattr(reg, "skill_body"):
                with contextlib.suppress(Exception):
                    body = reg.skill_body(nm) or body
            return f"# {skill.manifest.name}\n\n{body}"

        if name == "remedy_skill_run":
            if os.environ.get("REMEDY_MCP_ALLOW_RUN", "").strip() not in (
                "1",
                "true",
                "yes",
            ):
                return (
                    "skill_run disabled over MCP by default. "
                    "Set REMEDY_MCP_ALLOW_RUN=1 to allow script execution "
                    "(owner opt-in; still blocks quarantine)."
                )
            nm = str(args.get("name") or "").strip()
            script = str(args.get("script") or "").strip()
            skill = reg.get(nm) if nm else None
            if skill is None:
                return f"Skill not found: {nm}"
            meta = skill.manifest.metadata or {}
            if meta.get("quarantine"):
                return f"Skill '{nm}' is quarantined — Trust in Remedy first."
            scripts = list(skill.scripts or [])
            if not scripts and not script:
                return f"Skill '{nm}' has no scripts/ to run."
            # Resolve script path under skill dir
            skill_dir = None
            if skill.manifest.path:
                skill_dir = Path(skill.manifest.path)
                if skill_dir.is_file():
                    skill_dir = skill_dir.parent
            meta = skill.manifest.metadata or {}
            if skill_dir is None and meta.get("skill_path"):
                skill_dir = Path(str(meta["skill_path"])).parent
            if skill_dir is None:
                return f"Skill '{nm}' has no on-disk path for scripts."
            script_name = script or (scripts[0] if scripts else "")
            if not script_name:
                return f"Skill '{nm}' has no scripts/ to run."
            try:
                from remedy.skills.script_path import (
                    SkillScriptJailError,
                    resolve_jailed_skill_script,
                )

                script_path = resolve_jailed_skill_script(skill_dir, script_name)
            except SkillScriptJailError:
                return "Script path escapes skill scripts/ directory"
            if not script_path.is_file():
                return f"Script not found: {script_name}"
            try:
                import asyncio
                import shlex

                from remedy.skills.executor import SkillExecutor

                arg_list = shlex.split(str(args.get("args") or ""), posix=os.name != "nt")
                ex = SkillExecutor()
                result = asyncio.run(
                    ex.run_script(script_path, args=arg_list, timeout=120.0)
                )
                out = (result.stdout or "") + (result.stderr or "")
                if result.error:
                    out = (out + "\n" + result.error).strip()
                if not result.success:
                    return f"skill_run failed (exit {result.exit_code}): {out[:4000]}"
                return (out or "(no output)")[:8000]
            except Exception as e:
                return f"skill_run failed: {e}"

        if name == "remedy_plan_list":
            from remedy.core.plan_store import PlanStore

            store = PlanStore(_home())
            limit = max(1, min(int(args.get("limit") or 10), 30))
            plans = store.list_plans(limit=limit)
            if not plans:
                return "No plans saved."
            return "Plans:\n" + "\n".join(
                f"- [{p.status}] {p.title} (id={p.id}, steps={len(p.steps)})"
                for p in plans
            )

        if name == "remedy_plan_show":
            from remedy.core.plan_store import PlanStore

            store = PlanStore(_home())
            pid = str(args.get("plan_id") or "").strip()
            plan = store.get(pid) if pid else None
            if plan is None:
                plans = store.list_plans(limit=1)
                plan = plans[0] if plans else None
            if plan is None:
                return "No plan found."
            return plan.summary_markdown()

        return f"Unknown tool: {name}"

    @staticmethod
    def _error(mid: Any, code: int, message: str) -> dict[str, Any]:
        return {
            "jsonrpc": "2.0",
            "id": mid,
            "error": {"code": code, "message": message},
        }


def run_stdio_server() -> int:
    """Read JSON-RPC lines from stdin, write responses to stdout. Never log to stdout."""
    # Logging must go to stderr so we don't corrupt the MCP stream
    logging.basicConfig(stream=sys.stderr, level=logging.INFO)
    server = RemedyMCPServer()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            err = server._error(None, -32700, "Parse error")
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()
            continue
        resp: dict[str, Any] | None = server.handle(msg)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


def main(argv: list[str] | None = None) -> int:
    _ = argv
    return run_stdio_server()


if __name__ == "__main__":
    raise SystemExit(main())
