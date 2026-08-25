"""Live turn_pipeline: policy gate + hive cap bind + web provenance."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from remedy.core.approvals import APPROVALS
from remedy.core.turn_context import begin_turn, end_turn
from remedy.core.turn_pipeline import (
    _current_action,
    authorize_tool,
    bound_hive_capabilities,
    finish_tool,
    snapshot_live_turn,
)
from remedy.execution.action import ActionState
from remedy.policy.capabilities import Capability


@pytest.fixture
def _isolated_ask(monkeypatch):
    """Don't let ~/.remedy approval_mode override the in-memory Ask setting."""
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: {"access_scope": "project"},
    )
    prev = APPROVALS.mode
    APPROVALS.set_mode("ask")
    yield
    APPROVALS.set_mode(prev)


def test_authorize_file_read_proceeds(_isolated_ask):
    tokens = begin_turn("pipe-s", project_raw=None, active_path=".")
    try:
        snapshot_live_turn()
        args = {"path": "README.md"}
        assert authorize_tool(None, "file_read", args) is None
        assert "_action_id" not in args
    finally:
        end_turn("pipe-s", *tokens)


def test_authorize_bash_asks_in_ask_mode(_isolated_ask):
    tokens = begin_turn("pipe-ask", project_raw=None, active_path=".")
    try:
        args = {"command": "echo hi"}
        out = authorize_tool(None, "bash_exec", args)
        assert out is not None
        assert "APPROVAL_REQUIRED" in out
        assert "_action_id" not in args
    finally:
        end_turn("pipe-ask", *tokens)


def test_finish_tool_completes_the_authorized_action(_isolated_ask):
    tokens = begin_turn("pipe-fin", project_raw=None, active_path=".")
    try:
        args = {"path": "README.md"}
        assert authorize_tool(None, "file_read", args) is None
        rec = _current_action.get()
        assert rec is not None
        assert rec.state == ActionState.RUNNING
        out = finish_tool(None, "file_read", args, "hello", ok=True)
        assert out == "hello"
        assert rec.state == ActionState.COMPLETED
        assert _current_action.get() is None
        assert "_action_id" not in args
    finally:
        end_turn("pipe-fin", *tokens)


def test_hive_depth_denies_credential_tools(_isolated_ask):
    from remedy.core.hive.policy import hive_depth, reset_hive_depth, set_hive_depth

    tokens = begin_turn("pipe-hive", project_raw=None, active_path=".")
    depth = set_hive_depth(1)
    try:
        assert hive_depth() == 1
        out = authorize_tool(None, "self_improve_submit_pr", {"title": "x"})
        assert out is not None
        assert "HIVE_CAPABILITY" in out or "cannot use credentials" in out.lower()
    finally:
        reset_hive_depth(depth)
        end_turn("pipe-hive", *tokens)


def test_hive_daughters_lose_credential_use():
    caps = bound_hive_capabilities()
    assert "fs.read" in caps
    assert "fs.write" in caps
    assert "credential.use" not in caps
    assert "transact" not in caps
    assert "communicate" not in caps
    assert "computer.input" not in caps
    assert "browser.write" not in caps
    assert Capability.CREDENTIAL_USE.value not in caps


def test_hive_journal_caps_deny_undeclared(_isolated_ask):
    from remedy.core.hive.policy import (
        reset_hive_depth,
        reset_hive_granted,
        set_hive_depth,
        set_hive_granted,
    )
    from remedy.policy.capabilities import Capability as Cap

    tokens = begin_turn("pipe-hive-journal", project_raw=None, active_path=".")
    depth = set_hive_depth(1)
    granted = set_hive_granted(frozenset({Cap.FS_READ}))
    try:
        out = authorize_tool(None, "file_write", {"path": "x.txt", "content": "a"})
        assert out is not None
        assert "HIVE_CAPABILITY" in out
        assert "fs.write" in out
        ok = authorize_tool(None, "file_read", {"path": "x.txt"})
        assert ok is None
    finally:
        reset_hive_granted(granted)
        reset_hive_depth(depth)
        end_turn("pipe-hive-journal", *tokens)


def test_hive_depth_denies_mail_send(_isolated_ask):
    from remedy.core.hive.policy import reset_hive_depth, set_hive_depth

    tokens = begin_turn("pipe-hive-mail", project_raw=None, active_path=".")
    depth = set_hive_depth(1)
    try:
        out = authorize_tool(None, "mail_send", {"to": "a@b.c", "subject": "hi"})
        assert out is not None
        assert "HIVE_CAPABILITY" in out or "cannot use credentials" in out.lower()
    finally:
        reset_hive_depth(depth)
        end_turn("pipe-hive-mail", *tokens)


def test_authorize_mail_consumes_one_shot(_isolated_ask):
    from remedy.core.turn_pipeline import gate_already_passed

    tokens = begin_turn("pipe-mail", project_raw=None, active_path=".")
    try:
        args = {"to": "a@b.c", "subject": "hi"}
        out = authorize_tool(None, "mail_send", args)
        assert out is not None
        assert "APPROVAL_REQUIRED" in out
        aid = out.split("id=", 1)[1].split()[0].strip()
        APPROVALS.resolve(aid, approve=True)
        retry = authorize_tool(None, "mail_send", args)
        assert retry is None
        assert gate_already_passed("mail_send")
    finally:
        end_turn("pipe-mail", *tokens)


def test_snapshot_does_not_raise():
    tokens = begin_turn("pipe-snap", project_raw=None, active_path=".")
    try:
        snapshot_live_turn(SimpleNamespace(access_scope=lambda: "full"))
    finally:
        end_turn("pipe-snap", *tokens)
