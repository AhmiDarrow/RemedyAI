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
    current_plan_mode,
    current_turn_tool_steps,
    current_turn_workspace,
    end_turn,
    is_session_streaming,
    is_turn_aborted,
    register_turn_process,
    release_session_stream_claim,
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
