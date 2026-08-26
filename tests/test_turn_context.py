"""Cooperative abort / turn context registry."""

from __future__ import annotations

import asyncio
import contextlib
import sys
from unittest.mock import MagicMock

import pytest

from remedy.core.turn_context import (
    abort_session,
    begin_turn,
    current_chat_mode,
    current_last_user_text,
    current_plan_mode,
    current_turn_approval_mode,
    current_turn_tool_steps,
    current_turn_workspace,
    end_turn,
    is_session_streaming,
    is_turn_aborted,
    register_turn_process,
    release_session_stream_claim,
    set_turn_last_user_text,
    stream_claim_epoch,
    try_claim_session_stream,
    turn_session_id,
    unregister_turn_process,
)


@pytest.mark.asyncio
async def test_abort_sets_event_and_clears_registry():
    toks = begin_turn(
        "sess-1", project_raw=None, active_path="/tmp/ws"
    )
    try:
        assert is_session_streaming("sess-1")
        assert not is_turn_aborted()
        n = abort_session("sess-1")
        assert n == 1
        assert is_turn_aborted()
    finally:
        end_turn("sess-1", *toks)
    assert not is_session_streaming("sess-1")


@pytest.mark.asyncio
async def test_abort_unknown_session_is_noop():
    assert abort_session("missing") == 0


def test_try_claim_session_stream_is_atomic():
    sid = "claim-race"
    try:
        assert try_claim_session_stream(sid) is True
        assert is_session_streaming(sid) is True
        assert try_claim_session_stream(sid) is False
    finally:
        release_session_stream_claim(sid)
    assert is_session_streaming(sid) is False
    assert try_claim_session_stream(sid) is True
    release_session_stream_claim(sid)


def test_abort_keeps_claim_until_release():
    """Stop must not drop the claim — overlapping send would share the ReAct loop."""
    sid = "claim-abort-setup"
    assert try_claim_session_stream(sid) is True
    epoch = stream_claim_epoch(sid)
    assert is_session_streaming(sid) is True
    assert abort_session(sid) == 0  # no registered turn yet
    assert is_session_streaming(sid) is True
    assert try_claim_session_stream(sid) is False
    release_session_stream_claim(sid, epoch=epoch)
    assert is_session_streaming(sid) is False
    assert try_claim_session_stream(sid) is True
    release_session_stream_claim(sid)


def test_stale_epoch_abort_does_not_kill_newer_claim():
    sid = "claim-epoch"
    assert try_claim_session_stream(sid) is True
    e1 = stream_claim_epoch(sid)
    release_session_stream_claim(sid, epoch=e1)
    assert try_claim_session_stream(sid) is True
    e2 = stream_claim_epoch(sid)
    assert e2 != e1
    toks = begin_turn(sid, project_raw=None, active_path=".")
    try:
        assert abort_session(sid, epoch=e1) == 0
        assert is_session_streaming(sid) is True
        assert is_turn_aborted() is False
    finally:
        end_turn(sid, *toks)
        release_session_stream_claim(sid, epoch=e2)


@pytest.mark.asyncio
async def test_turn_workspace_isolated():
    t1 = begin_turn("a", project_raw="/proj-a", active_path="/proj-a")
    assert current_turn_workspace() is not None
    assert current_turn_workspace().active_path == "/proj-a"
    end_turn("a", *t1)
    assert current_turn_workspace() is None


def test_last_user_text_is_per_turn_not_runtime():
    """A sibling tab's prompt must not leak into this turn's context."""
    from types import SimpleNamespace

    runtime = SimpleNamespace(_last_user_text="tab-A secret prompt")
    toks = begin_turn("tab-b", project_raw=None, active_path=".")
    try:
        assert current_last_user_text(runtime) == ""
        set_turn_last_user_text("tab-B actual prompt", runtime)
        assert current_last_user_text(runtime) == "tab-B actual prompt"
        assert runtime._last_user_text == "tab-B actual prompt"
    finally:
        end_turn("tab-b", *toks)
    # Outside a turn, the runtime field is the legacy fallback.
    assert current_last_user_text(runtime) == "tab-B actual prompt"


def test_create_session_integrity_race(tmp_path):
    """Concurrent create with same id returns existing (no crash)."""

    from remedy.memory.store import MemoryStore
    from remedy.models import ChatSession

    async def _run():
        store = MemoryStore(tmp_path / "mem.db")
        await store.initialize()
        a = ChatSession(id="same-id", title="A")
        b = ChatSession(id="same-id", title="B")
        s1 = await store.create_chat_session(a)
        s2 = await store.create_chat_session(b)
        assert s1.id == s2.id == "same-id"
        # First writer wins title
        assert s2.title == "A"

    asyncio.run(_run())


def test_turn_session_id_prefers_contextvar():
    runtime = MagicMock()
    runtime._session_id = "runtime-stale"
    tok = begin_turn("ctx-session", project_raw=None, active_path=".")
    try:
        assert turn_session_id(runtime) == "ctx-session"
        assert turn_session_id(None) == "ctx-session"
    finally:
        end_turn("ctx-session", *tok)
    assert turn_session_id(runtime) == "runtime-stale"


def test_chat_mode_isolated_per_turn():

    t_chat = begin_turn("chat-sess", project_raw=None, active_path=".", chat_mode=True)
    try:
        assert current_chat_mode() is True
        assert current_plan_mode() is False
    finally:
        end_turn("chat-sess", *t_chat)
    t_build = begin_turn("build-sess", project_raw=None, active_path=".")
    try:
        assert current_chat_mode() is False
    finally:
        end_turn("build-sess", *t_build)


def test_plan_mode_and_tool_steps_isolated_per_turn():
    """Concurrent turns must not share plan_mode or the tool-step list."""
    t_plan = begin_turn("plan-sess", project_raw=None, active_path=".", plan_mode=True)
    try:
        assert current_plan_mode() is True
        steps = current_turn_tool_steps()
        steps.append({"tool": "plan_save"})
        assert len(current_turn_tool_steps()) == 1
    finally:
        end_turn("plan-sess", *t_plan)

    t_build = begin_turn(
        "build-sess", project_raw=None, active_path=".", plan_mode=False
    )
    try:
        assert current_plan_mode() is False
        assert current_turn_tool_steps() == []
    finally:
        end_turn("build-sess", *t_build)


def test_approval_mode_snapped_at_begin_turn():
    from remedy.core.approvals import APPROVALS

    prev = APPROVALS.mode
    try:
        APPROVALS.set_mode("ask")
        toks = begin_turn("jail-sess", project_raw=None, active_path=".")
        try:
            assert current_turn_approval_mode() == "ask"
            APPROVALS.set_mode("full")
            assert current_turn_approval_mode() == "ask"
        finally:
            end_turn("jail-sess", *toks)
        assert current_turn_approval_mode() is None
    finally:
        APPROVALS.set_mode(prev)


def test_continuity_objects_isolated_per_turn():
    """Session brief / partner / work roots freeze per turn ContextVar."""
    from remedy.core.turn_context import (
        turn_partner_state,
        turn_session_brief,
        turn_work_roots,
    )

    brief_a = MagicMock(name="brief-a", session_id="iso-a")
    partner_a = MagicMock(name="partner-a", session_id="iso-a")
    t_a = begin_turn(
        "iso-a",
        project_raw=None,
        active_path=".",
        session_brief=brief_a,
        partner_state=partner_a,
        work_roots=["/proj-a"],
    )
    try:
        assert turn_session_brief() is brief_a
        assert turn_partner_state() is partner_a
        assert turn_work_roots() == ["/proj-a"]
        assert turn_session_id() == "iso-a"

        brief_b = MagicMock(name="brief-b", session_id="iso-b")
        t_b = begin_turn(
            "iso-b",
            project_raw=None,
            active_path=".",
            session_brief=brief_b,
            partner_state=None,
            work_roots=["/proj-b"],
        )
        try:
            # Nested context (same task) sees B; after end, A restored.
            assert turn_session_brief() is brief_b
            assert turn_work_roots() == ["/proj-b"]
            assert turn_session_id() == "iso-b"
        finally:
            end_turn("iso-b", *t_b)

        assert turn_session_brief() is brief_a
        assert turn_partner_state() is partner_a
        assert turn_work_roots() == ["/proj-a"]
        assert turn_session_id() == "iso-a"
    finally:
        end_turn("iso-a", *t_a)


@pytest.mark.asyncio
async def test_abort_kills_registered_process():
    """abort_session should kill registered child processes (cooperative cancel)."""
    tok = begin_turn("kill-sess", project_raw=None, active_path=".")
    proc = None
    try:
        if sys.platform == "win32":
            # python -c busy wait is faster to kill than ping
            proc = await asyncio.create_subprocess_exec(
                sys.executable,
                "-c",
                "import time; time.sleep(60)",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        else:
            proc = await asyncio.create_subprocess_exec(
                "sleep",
                "60",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
        register_turn_process(proc)
        n = abort_session("kill-sess")
        assert n == 1
        assert is_turn_aborted()
        try:
            await asyncio.wait_for(proc.wait(), timeout=3.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
        assert proc.returncode is not None
    finally:
        if proc is not None:
            with contextlib.suppress(Exception):
                unregister_turn_process(proc)
        end_turn("kill-sess", *tok)


@pytest.mark.asyncio
async def test_sandbox_aborts_long_shell():
    """SubprocessSandbox should return aborted when turn is aborted mid-run."""
    from remedy.execution.sandbox import SubprocessSandbox

    tok = begin_turn("sand-abort", project_raw=None, active_path=".")
    try:
        sandbox = SubprocessSandbox(allowed_paths=[])
        cmd = [sys.executable, "-c", "import time; time.sleep(60)"]

        async def _abort_soon():
            await asyncio.sleep(0.15)
            abort_session("sand-abort")

        t = asyncio.create_task(_abort_soon())
        result = await sandbox.execute(cmd, timeout_seconds=10.0)
        await t
        assert result.exit_code == -1
        assert "Abort" in (result.stderr or "") or "abort" in (result.stderr or "").lower()
    finally:
        end_turn("sand-abort", *tok)


def test_react_flags_isolated_across_nested_turns():
    """Sibling tabs must not steal force_tool_choice / tier / IR."""
    from remedy.core.turn_context import (
        set_turn_action_ir,
        set_turn_force_tool_choice,
        set_turn_thinking_level,
        set_turn_tier,
        stash_pending_verify_remedy,
        take_pending_verify_remedy,
        turn_action_ir,
        turn_force_tool_choice,
        turn_thinking_level,
        turn_tier,
    )

    runtime = MagicMock()
    runtime._force_tool_choice = False
    runtime._turn_tier = 1
    runtime._thinking_level = "high"
    t_a = begin_turn("flag-a", project_raw=None, active_path=".")
    try:
        set_turn_force_tool_choice(True, runtime)
        set_turn_tier(3, runtime)
        set_turn_action_ir({"a": 1}, runtime)
        set_turn_thinking_level("off", runtime)
        assert turn_force_tool_choice(runtime) is True
        assert turn_tier(runtime) == 3
        assert turn_thinking_level(runtime) == "off"
        t_b = begin_turn("flag-b", project_raw=None, active_path=".")
        try:
            assert turn_force_tool_choice(runtime) is False
            assert turn_tier(runtime) == 1
            assert turn_action_ir(runtime) is None
            assert turn_thinking_level(runtime) == "high"
            set_turn_force_tool_choice(True, runtime)
            assert turn_force_tool_choice(runtime) is True
        finally:
            end_turn("flag-b", *t_b)
        # A's flags restored — B must not have overwritten them.
        assert turn_force_tool_choice(runtime) is True
        assert turn_tier(runtime) == 3
        assert turn_action_ir(runtime) == {"a": 1}
        assert turn_thinking_level(runtime) == "off"
    finally:
        end_turn("flag-a", *t_a)
    assert turn_force_tool_choice(runtime) is False

    stash_pending_verify_remedy("flag-a", "fix A")
    stash_pending_verify_remedy("flag-b", "fix B")
    assert take_pending_verify_remedy("flag-a") == "fix A"
    assert take_pending_verify_remedy("flag-a") is None
    assert take_pending_verify_remedy("flag-b") == "fix B"


def test_set_turn_flags_ignore_runtime_outside_turn():
    from remedy.core.turn_context import (
        set_turn_action_ir,
        set_turn_shadow_strict,
        set_turn_tier,
    )

    runtime = MagicMock()
    runtime._turn_tier = 1
    runtime._action_ir = None
    runtime._shadow_strict = False
    set_turn_tier(3, runtime)
    set_turn_action_ir({"x": 1}, runtime)
    set_turn_shadow_strict(True, runtime)
    assert runtime._turn_tier == 1
    assert runtime._action_ir is None
    assert runtime._shadow_strict is False


def test_context_snapshot_lives_on_turn_flags():
    from remedy.core.turn_context import (
        set_turn_context_snapshot,
        turn_context_snapshot,
    )

    runtime = MagicMock()
    runtime._last_context_snapshot = "global"
    t_a = begin_turn("snap-a", project_raw=None, active_path=".")
    try:
        set_turn_context_snapshot("a", runtime)
        assert turn_context_snapshot(runtime) == "a"
        t_b = begin_turn("snap-b", project_raw=None, active_path=".")
        try:
            set_turn_context_snapshot("b", runtime)
            assert turn_context_snapshot(runtime) == "b"
        finally:
            end_turn("snap-b", *t_b)
        assert turn_context_snapshot(runtime) == "a"
    finally:
        end_turn("snap-a", *t_a)


def test_skip_ask_is_turn_local(monkeypatch):
    from remedy.core.approvals import APPROVALS
    from remedy.core.turn_context import set_turn_skip_ask, turn_skip_ask

    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"approval_mode": "ask", "access_scope": "project"},
    )
    prev = APPROVALS.mode
    try:
        APPROVALS.set_mode("ask")
        t = begin_turn("skip-ask", project_raw=None, active_path=".")
        try:
            set_turn_skip_ask(True)
            assert turn_skip_ask() is True
            assert APPROVALS.needs_ask("echo hi", tool_name="file_write") is None
            assert APPROVALS.mode == "ask"
        finally:
            end_turn("skip-ask", *t)
        assert turn_skip_ask() is False
        assert APPROVALS.mode == "ask"
    finally:
        APPROVALS.set_mode(prev)


def test_write_budget_zero_does_not_inherit_runtime():
    from remedy.core.turn_context import (
        set_turn_sleev_force_direct,
        set_turn_write_budget,
        turn_write_budget,
    )

    runtime = MagicMock()
    runtime._remedy_write_budget = 8192
    runtime._sleev_force_direct = False
    t = begin_turn("wb-zero", project_raw=None, active_path=".")
    try:
        assert turn_write_budget(runtime) == 0
        set_turn_write_budget(4096, runtime)
        assert turn_write_budget(runtime) == 4096
        assert runtime._remedy_write_budget == 8192
        set_turn_sleev_force_direct(True, runtime)
        assert runtime._sleev_force_direct is False
        from remedy.core.turn_context import turn_sleev_force_direct

        assert turn_sleev_force_direct(runtime) is True
    finally:
        end_turn("wb-zero", *t)
    t2 = begin_turn("wb-sib", project_raw=None, active_path=".")
    try:
        assert turn_write_budget(runtime) == 0
    finally:
        end_turn("wb-sib", *t2)


def test_any_stream_claimed_sees_all_sessions():
    from remedy.core.turn_context import any_stream_claimed

    sid = "claim-any"
    try:
        assert any_stream_claimed() is False
        assert try_claim_session_stream(sid) is True
        assert any_stream_claimed() is True
    finally:
        release_session_stream_claim(sid)
    assert any_stream_claimed() is False
