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
    current_turn_workspace,
    end_turn,
    is_session_streaming,
    is_turn_aborted,
    register_turn_process,
    turn_session_id,
    unregister_turn_process,
)


@pytest.mark.asyncio
async def test_abort_sets_event_and_clears_registry():
    tok_s, tok_a, tok_w = begin_turn("sess-1", project_raw=None, active_path="/tmp/ws")
    try:
        assert is_session_streaming("sess-1")
        assert not is_turn_aborted()
        n = abort_session("sess-1")
        assert n == 1
        assert is_turn_aborted()
    finally:
        end_turn("sess-1", tok_s, tok_a, tok_w)
    assert not is_session_streaming("sess-1")


@pytest.mark.asyncio
async def test_abort_unknown_session_is_noop():
    assert abort_session("missing") == 0


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
