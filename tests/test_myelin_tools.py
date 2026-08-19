"""Myelin tools — crystallized skills, and the gate in front of running them.

A sheath is model-authored code that Remedy then executes locally, forever,
without asking a model again. That makes the approval gate the load-bearing
part of this module, and it is written to fail *closed*: if the approvals
machinery is missing or raises, the answer is BLOCKED, not "carry on". The
tests below spend most of their time on exactly that, because a gate that
fails open is indistinguishable from no gate at all right up until it matters.
"""

from __future__ import annotations

import json

import pytest

from remedy.core.agent_myelin_tools import register_myelin_tools

#: (tool, the arguments it actually takes) — each executes model-authored code.
EXECUTING_TOOLS = [
    ("myelin_crystallize", {"name": "x", "script": "print(1)", "test": "pass"}),
    ("myelin_run", {"slug": "x"}),
    ("myelin_verify", {"slug": "x"}),
]


class Reg:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}
        self.schemas: dict[str, dict] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler
        self.schemas[name] = parameters or {}


class RT:
    def __init__(self, home) -> None:
        self.tool_registry = Reg()
        self.config = type("C", (), {"home_dir": str(home)})()
        self._session_id = "myelin-session"


@pytest.fixture()
def myelin(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    rt = RT(tmp_path)
    register_myelin_tools(rt)
    return {"rt": rt, "tools": rt.tool_registry.tools, "home": tmp_path}


def allow(monkeypatch) -> None:
    monkeypatch.setattr(
        "remedy.core.agent_computer_tools._computer_approval_gate",
        lambda *a, **kw: None,
    )


def refuse(monkeypatch, message: str = "APPROVAL_REQUIRED id=1") -> None:
    monkeypatch.setattr(
        "remedy.core.agent_computer_tools._computer_approval_gate",
        lambda *a, **kw: message,
    )


def break_the_gate(monkeypatch) -> None:
    def boom(*a, **kw):
        raise RuntimeError("approvals subsystem is down")

    monkeypatch.setattr(
        "remedy.core.agent_computer_tools._computer_approval_gate", boom
    )


# --- the gate ---------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "kwargs"), EXECUTING_TOOLS)
async def test_a_refused_approval_stops_the_tool(myelin, monkeypatch, tool, kwargs):
    refuse(monkeypatch)
    assert "APPROVAL_REQUIRED" in await myelin["tools"][tool](**kwargs)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "kwargs"), EXECUTING_TOOLS)
async def test_a_broken_approvals_subsystem_blocks_rather_than_proceeds(
    myelin, monkeypatch, tool, kwargs
):
    """Fail closed. An unavailable gate must never read as permission."""
    break_the_gate(monkeypatch)
    out = await myelin["tools"][tool](**kwargs)
    assert out.startswith("BLOCKED")
    assert "RuntimeError" in out
    assert "do not retry blindly" in out


@pytest.mark.asyncio
async def test_the_block_message_names_the_tool_that_was_refused(myelin, monkeypatch):
    break_the_gate(monkeypatch)
    out = await myelin["tools"]["myelin_run"](slug="anything")
    assert "myelin_run" in out


@pytest.mark.asyncio
async def test_status_is_readable_without_approval(myelin, monkeypatch):
    """Listing what she has learned executes nothing, so it is never gated."""
    break_the_gate(monkeypatch)
    out = json.loads(await myelin["tools"]["myelin_status"]())
    assert isinstance(out, dict)


# --- what the gate is asked about -------------------------------------------


@pytest.mark.asyncio
async def test_the_gate_is_told_what_crystallizing_will_run(myelin, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        "remedy.core.agent_computer_tools._computer_approval_gate",
        lambda rt, name, summary: seen.append(summary) or "stop",
    )
    await myelin["tools"]["myelin_crystallize"](
        name="tidy-desk", script="print(1)", test="raise SystemExit(0)"
    )
    assert "tidy-desk" in seen[0]
    assert "runs the test" in seen[0]


@pytest.mark.asyncio
async def test_the_gate_is_told_which_sheath_will_run(myelin, monkeypatch):
    seen: list[str] = []
    monkeypatch.setattr(
        "remedy.core.agent_computer_tools._computer_approval_gate",
        lambda rt, name, summary: seen.append(summary) or "stop",
    )
    await myelin["tools"]["myelin_run"](slug="tidy-desk", args="--dry-run")
    assert "tidy-desk" in seen[0]
    assert "--dry-run" in seen[0]


@pytest.mark.asyncio
async def test_a_very_long_argument_is_truncated_in_the_approval_summary(
    myelin, monkeypatch
):
    """The owner reads this line; it must not be a wall of text."""
    seen: list[str] = []
    monkeypatch.setattr(
        "remedy.core.agent_computer_tools._computer_approval_gate",
        lambda rt, name, summary: seen.append(summary) or "stop",
    )
    await myelin["tools"]["myelin_run"](slug="s", args="x" * 5000)
    assert len(seen[0]) < 400


# --- argument handling ------------------------------------------------------


@pytest.mark.asyncio
async def test_a_missing_sheath_is_reported_not_raised(myelin, monkeypatch):
    allow(monkeypatch)
    out = json.loads(await myelin["tools"]["myelin_run"](slug="no-such-sheath"))
    assert out.get("ok") is not True


@pytest.mark.asyncio
async def test_verifying_a_missing_sheath_is_reported_not_raised(myelin, monkeypatch):
    allow(monkeypatch)
    out = json.loads(await myelin["tools"]["myelin_verify"](slug="no-such-sheath"))
    assert out.get("ok") is not True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('["--fast", "2"]', ["--fast", "2"]),
        ("--fast 2", ["--fast", "2"]),
        ("", []),
        ('"just a string"', ['"just a string"']),
    ],
)
async def test_argv_is_read_the_way_the_model_wrote_it(
    myelin, monkeypatch, raw, expected
):
    """JSON array, plain argv, or nothing — all three arrive constantly."""
    allow(monkeypatch)
    seen: list[list[str]] = []
    monkeypatch.setattr(
        "remedy.memory.myelin.run_sheath",
        lambda slug, args, home: seen.append(list(args)) or {"ok": False},
    )
    await myelin["tools"]["myelin_run"](slug="s", args=raw)
    assert seen[0] == expected


# --- registration -----------------------------------------------------------


def test_every_myelin_tool_is_registered(myelin):
    assert set(myelin["tools"]) == {
        "myelin_status",
        "myelin_crystallize",
        "myelin_run",
        "myelin_verify",
    }


@pytest.mark.parametrize(
    ("tool", "required"),
    [
        ("myelin_crystallize", ["name", "script", "test"]),
        ("myelin_run", ["slug"]),
        ("myelin_verify", ["slug"]),
    ],
)
def test_the_schemas_declare_what_they_need(myelin, tool, required):
    assert myelin["rt"].tool_registry.schemas[tool]["required"] == required


def test_status_needs_nothing(myelin):
    assert not myelin["rt"].tool_registry.schemas["myelin_status"].get("required")
