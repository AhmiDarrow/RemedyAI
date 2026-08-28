"""Sandbox communicate/abort must not leak pending _wait_abort tasks."""

from __future__ import annotations

import asyncio

import pytest

from remedy.execution.sandbox import _communicate_or_abort


class _HangProc:
    def __init__(self) -> None:
        self.returncode = None
        self._gate = asyncio.Event()

    async def communicate(self):
        await self._gate.wait()
        return b"out", b""


class _FastProc:
    returncode = 0

    async def communicate(self):
        return b"ok", b""


@pytest.mark.asyncio
async def test_timeout_awaits_abort_waiter(monkeypatch):
    monkeypatch.setattr("remedy.execution.process.kill_process_tree", lambda proc: None)
    abort = asyncio.Event()
    before = set(asyncio.all_tasks())
    out = await _communicate_or_abort(
        _HangProc(),  # type: ignore[arg-type]
        timeout_seconds=0.08,
        abort_event=abort,
    )
    assert out == (None, None)
    await asyncio.sleep(0)
    leftover = [
        t
        for t in asyncio.all_tasks() - before
        if not t.done() and "_wait_abort" in repr(t.get_coro())
    ]
    assert leftover == []


@pytest.mark.asyncio
async def test_abort_kills_and_does_not_leak(monkeypatch):
    monkeypatch.setattr("remedy.execution.process.kill_process_tree", lambda proc: None)
    abort = asyncio.Event()

    async def fire() -> None:
        await asyncio.sleep(0.02)
        abort.set()

    fire_task = asyncio.create_task(fire())
    before = set(asyncio.all_tasks())
    out = await _communicate_or_abort(
        _HangProc(),  # type: ignore[arg-type]
        timeout_seconds=2.0,
        abort_event=abort,
    )
    assert out == (None, None)
    await fire_task
    await asyncio.sleep(0)
    leftover = [
        t
        for t in asyncio.all_tasks() - before
        if not t.done() and "_wait_abort" in repr(t.get_coro())
    ]
    assert leftover == []


@pytest.mark.asyncio
async def test_success_reaps_abort_waiter(monkeypatch):
    monkeypatch.setattr("remedy.execution.process.kill_process_tree", lambda proc: None)
    abort = asyncio.Event()
    before = set(asyncio.all_tasks())
    out = await _communicate_or_abort(
        _FastProc(),  # type: ignore[arg-type]
        timeout_seconds=2.0,
        abort_event=abort,
    )
    assert out == (b"ok", b"")
    await asyncio.sleep(0)
    leftover = [
        t
        for t in asyncio.all_tasks() - before
        if not t.done() and "_wait_abort" in repr(t.get_coro())
    ]
    assert leftover == []
