"""App control — Remedy driving her own interface (surface switch, panels…)."""

from __future__ import annotations

import pytest

from remedy.core.app_control import (
    VALID_ACTIONS,
    VALID_PANELS,
    VALID_SURFACE_TARGETS,
    app_control_bus,
    infer_settings_section,
    normalize_panel,
    normalize_settings_section,
    normalize_surface_target,
    request_app_action,
)


def setup_function() -> None:
    app_control_bus().clear()


def test_switch_surface_enqueues():
    r = request_app_action("switch_surface", target="studio")
    assert r["ok"] is True
    assert r["command"]["action"] == "switch_surface"
    assert r["command"]["params"]["target"] == "studio"


def test_alongside_and_storyline_are_places_she_can_go():
    for place in ("alongside", "storyline", "grove", "home"):
        app_control_bus().clear()
        r = request_app_action("switch_surface", target=place)
        assert r["ok"] is True, place
        assert r["command"]["params"]["target"] == place


def test_normalize_surface_target_maps_owner_words():
    assert normalize_surface_target("Alongside") == "alongside"
    assert normalize_surface_target("home") == "grove"
    assert normalize_surface_target("studio") == "studio"
    assert normalize_surface_target("workbench") == "studio"
    assert normalize_surface_target("nope") is None
    assert "alongside" in VALID_SURFACE_TARGETS
    assert "storyline" in VALID_SURFACE_TARGETS


def test_unknown_action_refused():
    r = request_app_action("format_c_drive")
    assert r["ok"] is False
    assert "unknown" in r["error"]


def test_take_is_fifo_and_removes():
    request_app_action("switch_surface", target="grove")
    request_app_action("focus_composer")
    bus = app_control_bus()
    first = bus.take()
    second = bus.take()
    assert first["action"] == "switch_surface"
    assert second["action"] == "focus_composer"
    assert bus.take() is None  # drained


def test_peek_does_not_remove():
    request_app_action("new_session")
    bus = app_control_bus()
    assert bus.peek()["action"] == "new_session"
    assert bus.peek()["action"] == "new_session"  # still there
    assert bus.take()["action"] == "new_session"


def test_none_params_dropped():
    r = request_app_action("open_settings")
    assert r["ok"] is True
    assert "section" not in r["command"]["params"]


def test_all_valid_actions_enqueue():
    for act in VALID_ACTIONS:
        app_control_bus().clear()
        assert request_app_action(act)["ok"] is True


def test_queue_is_capped():
    for _i in range(100):
        request_app_action("focus_composer")
    # deque(maxlen=32) — never unbounded
    bus = app_control_bus()
    n = 0
    while bus.take() is not None:
        n += 1
    assert n <= 32


def _settings_rt():
    from remedy.core.agent_settings_tools import register_settings_tools
    from remedy.skills.tool_registry import ToolRegistry

    class RT:
        def __init__(self) -> None:
            self.tool_registry = ToolRegistry()

    rt = RT()
    register_settings_tools(rt)
    return rt


@pytest.mark.asyncio
async def test_app_control_tool_accepts_alongside():
    import json

    rt = _settings_rt()
    raw = await rt.tool_registry.execute(
        "app_control", action="switch_surface", target="alongside"
    )
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["command"]["params"]["target"] == "alongside"


def test_normalize_panel_and_settings_section():
    assert normalize_panel("Help") == "help"
    assert normalize_panel("time-travel") == "time_travel"
    assert normalize_panel("powershell") == "terminal"
    assert normalize_panel("browser") == "browser"
    assert normalize_panel("nope") is None
    assert "help" in VALID_PANELS
    assert normalize_settings_section("messengers") == "channels"
    assert normalize_settings_section("voice") == "voice"
    assert normalize_settings_section("appearance") == "theme"
    assert infer_settings_section({"llm_model": "grok-4"}) == "provider"
    assert infer_settings_section({"approval_mode": "ask"}) == "security-power"


@pytest.mark.asyncio
async def test_app_control_opens_settings_section_and_help():
    import json

    rt = _settings_rt()
    raw = await rt.tool_registry.execute(
        "app_control", action="open_settings", section="messengers"
    )
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["command"]["params"]["section"] == "channels"
    app_control_bus().clear()
    raw = await rt.tool_registry.execute(
        "app_control", action="open_panel", panel="terminal"
    )
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["command"]["params"]["panel"] == "terminal"
    app_control_bus().clear()
    raw = await rt.tool_registry.execute(
        "app_control", action="open_panel", panel="help", article="09-troubleshooting"
    )
    data = json.loads(raw)
    assert data["command"]["params"]["article"] == "09-troubleshooting"
    app_control_bus().clear()
    raw = await rt.tool_registry.execute(
        "app_control",
        action="open_panel",
        panel="files",
        path=r"C:\Users\Administrator\Desktop\example-folder",
    )
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["command"]["params"]["panel"] == "files"
    assert data["command"]["params"]["path"].endswith("example-folder")
    app_control_bus().clear()
    raw = await rt.tool_registry.execute(
        "app_control",
        action="open_panel",
        path=r"C:\Users\Administrator\Desktop\example-folder",
    )
    data = json.loads(raw)
    assert data["command"]["params"]["panel"] == "files"
    app_control_bus().clear()
    raw = await rt.tool_registry.execute(
        "app_control",
        action="open_panel",
        panel="browser",
        url="https://github.com/AhmiDarrow/RemedyAI",
    )
    data = json.loads(raw)
    assert data["command"]["params"]["url"].startswith("https://github.com/")
    app_control_bus().clear()
    raw = await rt.tool_registry.execute(
        "app_control",
        action="open_panel",
        panel="terminal",
        path=r"C:\Users\Administrator\Desktop\example-folder",
    )
    data = json.loads(raw)
    assert data["command"]["params"]["path"].endswith("example-folder")
    app_control_bus().clear()
    raw = await rt.tool_registry.execute(
        "app_control", action="open_session", session_id="4d89d9fa-a2a0-49e7-90c0-7e48732bfd1f"
    )
    data = json.loads(raw)
    assert data["ok"] is True
    assert data["command"]["action"] == "open_session"
    assert data["command"]["params"]["session_id"].startswith("4d89d9fa")
    assert request_app_action("close_ui")["ok"] is True


@pytest.mark.asyncio
async def test_list_sessions_tool_without_memory():
    rt = _settings_rt()
    out = await rt.tool_registry.execute("list_sessions")
    assert "not available" in out.lower() or "NO_MEMORY" in out


@pytest.mark.asyncio
async def test_app_control_tool_rejects_unknown_place():
    import json

    rt = _settings_rt()
    raw = await rt.tool_registry.execute(
        "app_control", action="switch_surface", target="minecraft"
    )
    data = json.loads(raw)
    assert data["ok"] is False
    assert "alongside" in data["error"]
