"""Family tests for the 2026-08-26 additive review follow-ups.

Each case pins the symptom family (not one reproduction string): wired
authorize_tool + handler, stored-key host check, HQ quality gate, hive
parent writes, persona wipe leftovers, live serve-lock PID, chat host default.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.approvals import APPROVALS, SENSITIVE_PREFIX
from remedy.core.hive.policy import is_mother_only_tool
from remedy.core.turn_context import begin_turn, end_turn
from remedy.core.turn_pipeline import _tool_command, authorize_tool, clear_tool_gate
from remedy.interfaces.instance_lock import (
    lock_path,
    release_serve_lock,
    try_acquire_serve_lock,
)
from remedy.memory.authority import may_write_parent_memory
from remedy.memory.persona_wipe import CONFIRM_PHRASE, wipe_persona
from remedy.memory.store import MemoryStore
from remedy.models import MemoryEntry, MemoryEntryType
from remedy.tools.catalog import descriptor_for


def test_computer_type_command_carries_vault_and_click_text() -> None:
    cmd = _tool_command({"text": "{{vault:card-visa}}"}, "computer_type")
    assert "vault:card-visa" in cmd
    assert "vault=card-visa" in cmd
    click = _tool_command({"click": "Place order"}, "computer_click")
    assert "Place order" in click


def test_computer_type_command_does_not_embed_typed_secrets() -> None:
    secret = "hunter2-correct-horse"
    cmd = _tool_command({"text": secret}, "computer_type")
    assert secret not in cmd
    assert f"chars={len(secret)}" in cmd


@pytest.mark.parametrize(
    "text",
    ("{{vault:card-visa}}", "{{ vault:amex }}", "prefix {{vault:card}} suffix"),
)
def test_authorize_tool_vault_family_is_owner_checkpoint(text: str, monkeypatch) -> None:
    """auto/full cannot skip vault — PolicyEngine sees the typed text."""
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "trust_profile": "autonomous"},
    )
    prev = APPROVALS.mode
    APPROVALS.set_mode("auto")
    tokens = begin_turn("vault-auth", project_raw=None, active_path=".")
    try:
        clear_tool_gate()
        out = authorize_tool(None, "computer_type", {"text": text})
        assert out is not None
        assert "APPROVAL_REQUIRED" in out
        assert SENSITIVE_PREFIX in out
    finally:
        end_turn("vault-auth", *tokens)
        APPROVALS.set_mode(prev)
        clear_tool_gate()


def test_vault_authorize_then_handler_is_one_owner_moment(monkeypatch) -> None:
    """Approve-once at PolicyEngine must not re-ask on the handler summary."""
    from remedy.core.agent_computer_tools import _computer_approval_gate

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "trust_profile": "autonomous"},
    )
    prev = APPROVALS.mode
    APPROVALS.set_mode("auto")
    tokens = begin_turn("vault-once", project_raw=None, active_path=".")
    try:
        clear_tool_gate()
        args = {"text": "{{vault:card-visa}}", "target": "auto"}
        first = authorize_tool(None, "computer_type", args)
        assert first is not None
        assert "APPROVAL_REQUIRED" in first
        aid = first.split("id=", 1)[1].split()[0].strip()
        APPROVALS.resolve(aid, approve=True)
        retry = authorize_tool(None, "computer_type", args)
        assert retry is None
        summary = "type chars=22 target=auto vault=card-visa"
        assert _computer_approval_gate(None, "computer_type", summary) is None
    finally:
        end_turn("vault-once", *tokens)
        APPROVALS.set_mode(prev)
        clear_tool_gate()


def test_handler_vault_still_asks_when_authorize_saw_plain_text(monkeypatch) -> None:
    """PolicyEngine miss (plain type) must not waive a vault handler summary."""
    from remedy.core.agent_computer_tools import _computer_approval_gate

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "trust_profile": "autonomous"},
    )
    prev = APPROVALS.mode
    APPROVALS.set_mode("auto")
    tokens = begin_turn("vault-inner", project_raw=None, active_path=".")
    try:
        clear_tool_gate()
        assert authorize_tool(None, "computer_type", {"text": "hello"}) is None
        out = _computer_approval_gate(
            None, "computer_type", "type chars=22 target=auto vault=card-visa"
        )
        assert out is not None
        assert "APPROVAL_REQUIRED" in out
        assert SENSITIVE_PREFIX in out
    finally:
        end_turn("vault-inner", *tokens)
        APPROVALS.set_mode(prev)
        clear_tool_gate()


def test_ordinary_computer_type_in_auto_still_runs(monkeypatch) -> None:
    """Do not add Ask on every keystroke — only owner-checkpoint text."""
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project", "trust_profile": "autonomous"},
    )
    prev = APPROVALS.mode
    APPROVALS.set_mode("auto")
    tokens = begin_turn("type-auto", project_raw=None, active_path=".")
    try:
        clear_tool_gate()
        out = authorize_tool(None, "computer_type", {"text": "hello world"})
        assert out is None
    finally:
        end_turn("type-auto", *tokens)
        APPROVALS.set_mode(prev)
        clear_tool_gate()


def test_mcp_tools_are_mother_only() -> None:
    assert is_mother_only_tool("mcp_github_create_issue")
    assert not is_mother_only_tool("file_read")
    assert not is_mother_only_tool("web_search")


def test_self_inject_round_is_owner_lock() -> None:
    from remedy.core.approvals import ApprovalQueue
    from remedy.policy.capabilities import Capability

    assert "self_inject_round" in ApprovalQueue.OWNER_LOCK_TOOLS
    d = descriptor_for("self_inject_round")
    assert d.requires_approval
    assert Capability.CREDENTIAL_USE in d.capabilities


def test_self_inject_round_auto_cannot_waive(monkeypatch) -> None:
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    prev = APPROVALS.mode
    APPROVALS.set_mode("auto")
    tokens = begin_turn("inject-auto", project_raw=None, active_path=".")
    try:
        out = authorize_tool(None, "self_inject_round", {"tree": "python"})
        assert out is not None
        assert "APPROVAL_REQUIRED" in out
        assert SENSITIVE_PREFIX in out
    finally:
        end_turn("inject-auto", *tokens)
        APPROVALS.set_mode(prev)


def test_calendar_cancel_and_mail_disconnect_are_owner_checkpoints(monkeypatch) -> None:
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    prev = APPROVALS.mode
    APPROVALS.set_mode("full")
    tokens = begin_turn("cal-full", project_raw=None, active_path=".")
    try:
        for name in ("calendar_cancel_event", "mail_disconnect"):
            out = authorize_tool(None, name, {"event_id": "e1"})
            assert out is not None, name
            assert SENSITIVE_PREFIX in out, name
    finally:
        end_turn("cal-full", *tokens)
        APPROVALS.set_mode(prev)


def test_models_get_refuses_stored_key_to_foreign_host(monkeypatch) -> None:
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    import remedy.interfaces.routes.catalog as catalog_mod
    from remedy.interfaces.routes.catalog import register_catalog_routes

    monkeypatch.setattr(
        catalog_mod,
        "load_config",
        lambda: {
            "llm_provider": "openai",
            "llm_base_url": "https://api.openai.com/v1",
        },
    )
    monkeypatch.setattr(
        "remedy.interfaces.config.resolve_provider_api_key",
        lambda cfg, provider: "sk-not-a-real-key-for-tests",
    )
    app = FastAPI()
    register_catalog_routes(app, runtime=None, gateway=None, memory=None)
    r = TestClient(app).get(
        "/api/models",
        params={"provider": "openai", "base_url": "http://127.0.0.1:9/v1"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body.get("models") == []
    assert "Refused" in str(body.get("error") or "")


def test_synthesize_standard_quality_skips_chatterbox_even_if_ready(
    tmp_path: Path, monkeypatch
) -> None:
    import remedy.voice.chatterbox as hq
    import remedy.voice.service as svc

    svc.save_voice_settings({"tts_quality": "standard"}, tmp_path)
    monkeypatch.setattr(hq, "chatterbox_ready", lambda home_dir=None: True)
    monkeypatch.setattr(
        hq,
        "synthesize",
        lambda *a, **k: (_ for _ in ()).throw(
            AssertionError("chatterbox must not run when quality is standard")
        ),
    )
    monkeypatch.setattr(svc, "get_tts_engine", lambda home_dir=None: None)
    monkeypatch.setattr(svc, "_managed", lambda: False)
    assert svc.synthesize("hello", gender="female", home_dir=tmp_path) is None


def test_hive_writer_cannot_write_parent_memory() -> None:
    assert may_write_parent_memory("hive_abc") is False
    assert may_write_parent_memory("sess-1") is True


@pytest.mark.asyncio
async def test_persona_wipe_clears_notes_and_soul_files(tmp_path: Path) -> None:
    store = MemoryStore(tmp_path / "memory.db")
    await store.initialize()
    await store.upsert(
        MemoryEntry(
            title="tea",
            content="likes tea",
            entry_type=MemoryEntryType.NOTE,
        )
    )
    soul = tmp_path / "soul"
    soul.mkdir()
    (soul / "embeddings.json").write_text('{"t":"likes tea"}', encoding="utf-8")
    (soul / "field.json").write_text("{}", encoding="utf-8")
    stats = await wipe_persona(store, home=tmp_path, confirm=CONFIRM_PHRASE)
    assert stats["ok"] is True
    leftover = await store.list_by_type(MemoryEntryType.NOTE, limit=10)
    assert leftover == []
    assert not (soul / "embeddings.json").is_file()
    await store.close()


def test_live_pid_lock_is_not_stolen(tmp_path: Path, monkeypatch) -> None:
    import os

    home = tmp_path / "live"
    home.mkdir()
    p = lock_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(f"{os.getpid()} 1.0\n", encoding="utf-8")
    ok, msg = try_acquire_serve_lock(home)
    # Same PID is allowed to re-enter; a *different* live PID must not steal.
    assert ok is True, msg
    release_serve_lock()


def test_foreign_live_pid_lock_is_not_stolen(tmp_path: Path, monkeypatch) -> None:
    home = tmp_path / "foreign"
    home.mkdir()
    p = lock_path(home)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text("1 1.0\n", encoding="utf-8")
    monkeypatch.setattr(
        "remedy.interfaces.instance_lock._pid_alive", lambda pid: pid == 1
    )
    ok, msg = try_acquire_serve_lock(home)
    assert ok is False
    assert "already running" in msg.lower() or "pid=1" in msg


def test_linux_desktop_module_exports_capture_and_input() -> None:
    from remedy.core.computer import desktop_linux as lin
    from remedy.core.computer.desktop_os import native

    assert callable(lin.screenshot_png)
    assert callable(lin.click)
    assert callable(lin.type_text)
    assert callable(lin.open_app)
    m = native()
    assert m is not None


def test_chat_computer_host_defaults_off() -> None:
    from remedy.interfaces.cli.parser import build_parser

    p = build_parser()
    ns = p.parse_args(["chat"])
    assert bool(getattr(ns, "computer_host", False)) is False


def test_messenger_inbound_does_not_call_last_desktop_muscle(monkeypatch) -> None:
    """Guard: handle path no longer rewrites llm_provider from last_llm_provider."""
    import inspect

    from remedy.gateway import session_bridge as sb

    src = inspect.getsource(sb.handle_messenger_event)
    assert "update_chat_session" not in src or "llm_provider=last_p" not in src


def test_browser_vault_type_requires_ref(tmp_path: Path) -> None:
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    ex = ComputerExecutor(tmp_path)
    out = ex._run_browser(ComputerAction.TYPE, text="{{vault:card-visa}}")
    assert out.get("ok") is False
    extra = out.get("extra") or {}
    assert extra.get("needs") == "ref" or "named field" in str(out.get("message") or "").lower()


@pytest.mark.parametrize("query", ["browser", "desktop", "auto", "rail", "os"])
def test_browser_vault_type_rejects_routing_token_query(tmp_path: Path, query: str) -> None:
    """query=browser is a drive target, not a field — must not unlock vault type."""
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    ex = ComputerExecutor(tmp_path)
    out = ex._run_browser(
        ComputerAction.TYPE, text="{{vault:card-visa}}", query=query
    )
    assert out.get("ok") is False, out
    assert "named field" in str(out.get("message") or "").lower()


def test_act_vault_type_refuses_without_a_named_field(tmp_path: Path, monkeypatch) -> None:
    from remedy.core.computer.executor import ComputerExecutor
    from remedy.core.computer.types import ComputerAction

    ex = ComputerExecutor(tmp_path)
    monkeypatch.setattr(
        ex,
        "_expand_vault_text",
        lambda text, **_k: (str(text).replace("{{vault:card-visa}}", "4111"), None),
    )
    out = ex._run_browser(
        ComputerAction.ACT, type="{{vault:card-visa}}", goal="fill the card"
    )
    assert out.get("ok") is False, out
    assert "named field" in str(out.get("message") or "").lower()


def test_resolve_tools_does_not_or_task_detector() -> None:
    import inspect

    from remedy.core import react_turn
    from remedy.core.react_turn import resolve_tools

    src = inspect.getsource(react_turn.resolve_tools)
    assert "msg_wants = msg_wants or task_like" not in src
    all_t = [
        {
            "type": "function",
            "function": {"name": "file_write", "parameters": {"type": "object"}},
        }
    ]
    laugh = resolve_tools(
        message="make me laugh",
        all_tools=all_t,
        turn_tier=1,
    )
    assert laugh.reason in ("no_work_request", "l1_pure_chat", "non_work")
    assert laugh.pack in ("none", "peek")
    game = resolve_tools(
        message="make a tiny platformer in godot",
        all_tools=all_t,
        turn_tier=1,
    )
    assert game.tools is not None
    assert game.reason != "no_work_request"
