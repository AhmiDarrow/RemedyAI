"""Live turn_pipeline: policy gate + hive cap bind + web provenance."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.approvals import APPROVALS
from remedy.core.turn_context import begin_turn, end_turn
from remedy.core.turn_pipeline import authorize_tool, bound_hive_capabilities, snapshot_live_turn
from remedy.policy.capabilities import Capability


def test_authorize_file_read_proceeds():
    prev = APPROVALS.mode
    APPROVALS.set_mode("ask")
    tokens = begin_turn("pipe-s", project_raw=None, active_path=".")
    try:
        snapshot_live_turn()
        assert authorize_tool(None, "file_read", {"path": "README.md"}) is None
    finally:
        end_turn("pipe-s", *tokens)
        APPROVALS.set_mode(prev)


def test_authorize_bash_asks_in_ask_mode():
    prev = APPROVALS.mode
    APPROVALS.set_mode("ask")
    tokens = begin_turn("pipe-ask", project_raw=None, active_path=".")
    try:
        out = authorize_tool(None, "bash_exec", {"command": "echo hi"})
        assert out is not None
        assert "APPROVAL_REQUIRED" in out
    finally:
        end_turn("pipe-ask", *tokens)
        APPROVALS.set_mode(prev)


def test_hive_daughters_lose_credential_use():
    caps = bound_hive_capabilities()
    assert "fs.read" in caps
    assert "credential.use" not in caps
    assert Capability.CREDENTIAL_USE.value not in caps


def test_snapshot_does_not_raise():
    tokens = begin_turn("pipe-snap", project_raw=None, active_path=".")
    try:
        snapshot_live_turn(SimpleNamespace(access_scope=lambda: "full"))
    finally:
        end_turn("pipe-snap", *tokens)
