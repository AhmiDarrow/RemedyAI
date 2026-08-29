"""Casual writes actually run job_run kind=verify; Ask still gates."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from remedy.core.build_oracle import run_casual_verify
from remedy.core.jobs import JobResult
from remedy.core.react_loop.tool_batch import _batch_ran_verify


def test_batch_ran_verify_detects_job_run_kind():
    assert _batch_ran_verify(
        [{"function": {"name": "job_run", "arguments": {"kind": "verify"}}}]
    )
    assert not _batch_ran_verify(
        [{"function": {"name": "job_run", "arguments": {"kind": "explore"}}}]
    )
    assert not _batch_ran_verify(
        [{"function": {"name": "file_write", "arguments": {}}}]
    )


def test_casual_verify_runs_the_job(monkeypatch):
    called: dict = {}

    async def fake_job(runtime, *, command="", path="", timeout=180.0):
        called["command"] = command
        return JobResult(kind="verify", ok=True, summary="verify exit_code=0")

    monkeypatch.setattr("remedy.core.jobs.run_verify_job", fake_job)
    monkeypatch.setattr(
        "remedy.core.build_oracle.discover_verify_command",
        lambda *a, **k: "pytest -q",
    )
    out = asyncio.run(run_casual_verify(SimpleNamespace()))
    assert called["command"] == "pytest -q"
    assert out["ran"] is True
    assert out["ok"] is True
    assert "exited 0" in out["message"]


def test_casual_verify_keeps_ask_gate(monkeypatch):
    async def fake_job(runtime, *, command="", path="", timeout=180.0):
        return JobResult(
            kind="verify",
            ok=False,
            summary="APPROVAL_REQUIRED id=abc\nreason=Shell\ncommand=pytest -q",
        )

    monkeypatch.setattr("remedy.core.jobs.run_verify_job", fake_job)
    monkeypatch.setattr(
        "remedy.core.build_oracle.discover_verify_command",
        lambda *a, **k: "pytest -q",
    )
    out = asyncio.run(run_casual_verify(SimpleNamespace()))
    assert out["ran"] is False
    assert out["approval"] is True
    assert "APPROVAL_REQUIRED" in out["message"]
    assert "Do not claim green" in out["message"]


def test_casual_verify_silent_when_no_command(monkeypatch):
    monkeypatch.setattr(
        "remedy.core.build_oracle.discover_verify_command",
        lambda *a, **k: "",
    )
    out = asyncio.run(run_casual_verify(SimpleNamespace()))
    assert out["ran"] is False
    assert "No project test command" in out["message"]
