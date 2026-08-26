"""RMB as an organ: world map + the rmb tool (not Settings-only)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from remedy.core.agent_local_tools import register_rmb_tools
from remedy.core.metabolism.machine_map import get_machine_map, reset_machine_map
from remedy.core.metabolism.turn import begin_turn_metabolism
from remedy.interfaces.settings_apply import resolve_setup_phrase


class _Reg:
    def __init__(self) -> None:
        self.tools: dict[str, object] = {}

    def register_builtin_handler(self, name, description, handler, parameters=None):
        self.tools[name] = handler


class _RT:
    def __init__(self, home: Path) -> None:
        self.tool_registry = _Reg()
        self.config = type("C", (), {"home_dir": str(home)})()


@pytest.fixture()
def rmb_rt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_machine_map("rmb_org")
    rt = _RT(tmp_path)
    register_rmb_tools(rt)
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.get_rmb_status",
        lambda cfg=None: {
            "ok": True,
            "brand": "RMB",
            "brand_full": "Remedy Muscle Bridge",
            "enabled": True,
            "auto_start": False,
            "installed": True,
            "running": False,
            "ready": False,
            "starting": False,
            "loading": False,
            "user_stopped": True,
            "base_url": "http://127.0.0.1:8787/v1",
            "port": 8787,
            "model_id": "Qwen3.5-9B",
            "chat_model": "Qwen3.5-9B",
            "model_path": str(tmp_path / "models" / "Qwen3.5-9B.gguf"),
            "model_present": True,
            "runtime_present": True,
            "ctx_size": 8192,
            "profile": "autofit",
            "vision_suspended": False,
            "last_error": None,
        },
    )
    return rt


@pytest.mark.asyncio
async def test_rmb_status_does_not_claim_ready_when_stopped(rmb_rt):
    raw = await rmb_rt.tool_registry.tools["rmb"](action="status")
    d = json.loads(raw)
    assert d["ready"] is False
    assert d["running"] is False
    assert "SUCCESS" not in raw
    assert "rmb action=start" in (d.get("next") or "")


@pytest.mark.asyncio
async def test_rmb_unknown_action_names_the_family(rmb_rt):
    raw = await rmb_rt.tool_registry.tools["rmb"](action="explode")
    assert "Unknown rmb action" in raw
    assert "status" in raw and "start" in raw and "use" in raw


@pytest.mark.asyncio
async def test_rmb_search_without_query_says_how(rmb_rt):
    raw = await rmb_rt.tool_registry.tools["rmb"](action="search")
    assert "MISSING_QUERY" in raw or "query is required" in raw.lower()


def test_setup_phrase_rmb_does_not_silently_flip_provider():
    """RMB is not a config.toml toggle — the rmb tool starts the host."""
    assert resolve_setup_phrase("rmb") is None
    assert resolve_setup_phrase("enable rmb") is None


@pytest.mark.asyncio
async def test_update_settings_rmb_phrase_points_at_rmb_tool(tmp_path: Path):
    from remedy.core.agent_settings_tools import register_settings_tools

    class Reg:
        def __init__(self) -> None:
            self.tools = {}

        def register_builtin_handler(self, name, description, handler, parameters=None):
            self.tools[name] = handler

    rt = type("RT", (), {"tool_registry": Reg(), "memory": None, "gateway": None})()
    register_settings_tools(rt)
    out = await rt.tool_registry.tools["update_settings"](setup="start rmb")
    assert "rmb tool" in out.lower() or 'action="status"' in out


def test_house_map_tracks_rmb_organ(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    reset_machine_map("house_rmb")
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running",
        lambda *a, **k: False,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.config.load_rmb_json",
        lambda home=None: {
            "enabled": True,
            "model_id": "Qwen3.5-9B",
            "profile": "autofit",
            "ctx_size": 8192,
            "port": 8787,
        },
    )
    m = get_machine_map("house_rmb")
    m.refresh_house_organs(tmp_path)
    hint = m.organ_hint()
    assert hint.startswith("[House]")
    assert "RMB=stopped" in hint
    sys_hint = m.system_hint()
    assert "rmb=stopped" in sys_hint


def test_begin_turn_injects_house_rmb_when_home_known(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
):
    reset_machine_map("turn_rmb")
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.is_running",
        lambda *a, **k: True,
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.config.load_rmb_json",
        lambda home=None: {
            "enabled": True,
            "model_path": str(tmp_path / "Qwen.gguf"),
            "profile": "autofit",
            "ctx_size": 4096,
            "port": 8787,
        },
    )
    meta = begin_turn_metabolism(
        session_id="turn_rmb",
        user_text="start the local model",
        intent="tool",
        tools_enabled=True,
        home=tmp_path,
    )
    blob = " ".join(meta.get("injects") or [])
    assert "RMB=" in blob or "rmb=" in blob


def test_builtin_rmb_discover_spec_is_bundled_health():
    from remedy.core.local_discover import builtin_service_specs

    specs = {s.skill: s for s in builtin_service_specs()}
    rmb = specs["rmb"]
    svc = rmb.services[0]
    assert svc.ports == [8787]
    assert svc.path == "/health"
    assert not svc.entry_files
    assert svc.start_template == ""


@pytest.mark.asyncio
async def test_rmb_start_auto_does_not_invent_a_checkpoint(
    rmb_rt, monkeypatch: pytest.MonkeyPatch
):
    from remedy.core.approvals import APPROVALS

    monkeypatch.setattr(
        "remedy.runtime.rmb.service.apply_rmb_settings",
        lambda *a, **k: {"ok": True},
    )
    monkeypatch.setattr(
        "remedy.runtime.rmb.service.start_rmb_server",
        lambda *a, **k: {"ok": True, "running": True, "ready": True},
    )
    prev = APPROVALS.mode
    try:
        APPROVALS.set_mode("auto")
        raw = await rmb_rt.tool_registry.tools["rmb"](action="start")
        assert "APPROVAL_REQUIRED" not in raw
        APPROVALS.set_mode("ask")
        raw_ask = await rmb_rt.tool_registry.tools["rmb"](action="start")
        assert "APPROVAL_REQUIRED" in raw_ask
    finally:
        APPROVALS.set_mode(prev)


def test_builtin_vision_discover_spec_is_bundled_health():
    from remedy.core.local_discover import builtin_service_specs

    specs = {s.skill: s for s in builtin_service_specs()}
    vis = specs["vision"]
    svc = vis.services[0]
    assert svc.ports == [8740]
    assert svc.path == "/health"
    assert not svc.entry_files
