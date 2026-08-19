"""local_discover — how Remedy learns what is actually installed on a machine.

Every branch either returns JSON she can read or a structured error that names
the next move. The thing being guarded is the failure text: a discovery tool
that answers "error" without saying what to set sends the model off to
list_dir the whole disk, which is the exact behaviour the suggestion field
exists to prevent.
"""

from __future__ import annotations

import json

import pytest

from remedy.core.agent_local_tools import register_local_discover_tools


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
        self.skills = None


@pytest.fixture()
def discover(tmp_path, monkeypatch):
    rt = RT(tmp_path)
    register_local_discover_tools(rt)
    monkeypatch.setattr(
        "remedy.core.local_discover.discover_all",
        lambda **kw: {"comfyui": {"found": False}, "ollama": {"found": True}},
    )
    monkeypatch.setattr(
        "remedy.core.local_discover.discover_one",
        lambda target, **kw: {target: {"found": True, "url": "http://127.0.0.1:1"}},
    )
    monkeypatch.setattr(
        "remedy.core.local_discover.collect_skill_local_specs", lambda _s: []
    )
    return rt.tool_registry


# --- scanning ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_bare_call_scans_everything(discover):
    out = json.loads(await discover.tools["local_discover"]())
    assert set(out) == {"comfyui", "ollama"}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["scan", "status", "", "  SCAN  "])
async def test_the_scan_aliases_all_mean_the_same_thing(discover, action):
    out = json.loads(await discover.tools["local_discover"](action=action))
    assert "ollama" in out


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["one", "get", "find"])
async def test_a_single_target_can_be_probed(discover, action):
    out = json.loads(
        await discover.tools["local_discover"](action=action, target="comfyui")
    )
    assert out["comfyui"]["found"] is True


@pytest.mark.asyncio
async def test_asking_for_one_thing_without_naming_it_falls_back_to_a_scan(discover):
    out = json.loads(await discover.tools["local_discover"](action="one", target=""))
    assert set(out) == {"comfyui", "ollama"}


@pytest.mark.asyncio
async def test_an_unknown_action_scans_rather_than_erroring(discover):
    """Being wrong about the verb should not cost her the answer."""
    out = json.loads(await discover.tools["local_discover"](action="frobnicate"))
    assert "ollama" in out


@pytest.mark.asyncio
async def test_a_failing_probe_says_what_to_configure(discover, monkeypatch):
    def boom(**kw):
        raise RuntimeError("probe socket refused")

    monkeypatch.setattr("remedy.core.local_discover.discover_all", boom)
    out = await discover.tools["local_discover"]()
    assert "DISCOVER_ERROR" in out
    assert "COMFYUI_URL" in out
    assert "do not list_dir the whole disk" in out


# --- the home census --------------------------------------------------------


class Census:
    def to_dict(self):
        return {"rooms": ["study"], "tools": ["git"]}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["home", "census"])
async def test_the_census_is_read_not_rebuilt(discover, monkeypatch, action):
    calls: list[str] = []
    monkeypatch.setattr(
        "remedy.execution.host.stretch.load_census",
        lambda h: calls.append("load") or Census(),
    )
    monkeypatch.setattr(
        "remedy.execution.host.stretch.stretch_home",
        lambda h, force=False: pytest.fail("should not re-probe"),
    )
    out = json.loads(await discover.tools["local_discover"](action=action))
    assert out["rooms"] == ["study"]
    assert calls == ["load"]


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["stretch", "map"])
async def test_stretching_re_probes_the_machine(discover, monkeypatch, action):
    monkeypatch.setattr(
        "remedy.execution.host.stretch.stretch_home", lambda h, force=False: Census()
    )
    monkeypatch.setattr(
        "remedy.execution.host.stretch.load_census",
        lambda h: pytest.fail("should have re-probed"),
    )
    out = json.loads(await discover.tools["local_discover"](action=action))
    assert out["tools"] == ["git"]


@pytest.mark.asyncio
async def test_a_home_that_was_never_stretched_says_how_to_stretch_it(
    discover, monkeypatch
):
    monkeypatch.setattr("remedy.execution.host.stretch.load_census", lambda h: None)
    out = await discover.tools["local_discover"](action="home")
    assert "NO_CENSUS" in out
    assert "action=stretch" in out


@pytest.mark.asyncio
async def test_a_failed_stretch_is_reported_as_a_stretch_failure(discover, monkeypatch):
    def boom(h, force=False):
        raise OSError("WMI unavailable")

    monkeypatch.setattr("remedy.execution.host.stretch.stretch_home", boom)
    out = await discover.tools["local_discover"](action="stretch")
    assert "STRETCH_FAILED" in out
    assert "WMI unavailable" in out


# --- registration -----------------------------------------------------------


def test_the_tool_is_registered_with_a_schema(tmp_path):
    rt = RT(tmp_path)
    register_local_discover_tools(rt)
    assert "local_discover" in rt.tool_registry.tools
    assert rt.tool_registry.schemas["local_discover"]["type"] == "object"


def test_a_runtime_without_skills_still_registers(tmp_path):
    rt = RT(tmp_path)
    del rt.skills
    register_local_discover_tools(rt)
    assert "local_discover" in rt.tool_registry.tools
