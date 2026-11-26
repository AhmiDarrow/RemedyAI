"""Phase 5 tests: Execution sandbox, tool runtime, and PolicyEngine gate."""

from __future__ import annotations

import pytest

from remedy.execution.result import ExecutionResult, SubprocessSandbox
from remedy.policy.decisions import ToolRequest
from remedy.policy.engine import PolicyEngine
from tests.conftest import core_available

requires_core = pytest.mark.skipif(
    not core_available(), reason="remedy_core not built in this environment"
)


class TestPolicyEngineGate:
    """Tool allow/deny/ask via PolicyEngine (execution.policy retired)."""

    def test_safe_tool_allowed(self):
        decision = PolicyEngine().evaluate(None, "memory_search", ToolRequest(name="memory_search"))
        assert decision.allowed
        assert not decision.requires_approval

    def test_dangerous_bash_denied(self):
        decision = PolicyEngine().evaluate(
            None,
            "bash_exec",
            ToolRequest(name="bash_exec", arguments={"command": "sudo id"}),
        )
        assert not decision.allowed
        assert decision.reason

    def test_mail_send_always_asks(self):
        decision = PolicyEngine().evaluate(None, "mail_send", ToolRequest(name="mail_send"))
        assert decision.allowed
        assert decision.requires_approval


class TestSubprocessSandbox:
    """Subprocess execution tests."""

    @pytest.mark.asyncio
    async def test_echo_command(self):
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(["python", "-c", "print('hello')"])
        assert result.exit_code == 0
        assert "hello" in result.stdout

    @pytest.mark.asyncio
    async def test_nonzero_exit(self):
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(["python", "-c", "import sys; sys.exit(42)"])
        assert result.exit_code == 42

    @pytest.mark.asyncio
    async def test_stderr_capture(self):
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(
            ["python", "-c", "import sys; print('error', file=sys.stderr)"]
        )
        assert "error" in result.stderr

    @pytest.mark.asyncio
    async def test_command_not_found(self):
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(["nonexistent_binary_xyz"])
        assert result.exit_code == -1
        assert "not found" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_permission_denied_missing_name_is_not_found(self, monkeypatch):
        """WSL often raises EACCES for an unknown PATH name, not ENOENT."""
        import remedy.execution.process as process_mod

        async def _boom(*_a, **_k):
            raise PermissionError(13, "Permission denied", "nonexistent_binary_xyz")

        monkeypatch.setattr(process_mod, "run_hidden_async", _boom)
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(["nonexistent_binary_xyz"])
        assert result.exit_code == -1
        assert "not found" in result.stderr.lower()

    @pytest.mark.asyncio
    async def test_timeout(self):
        sandbox = SubprocessSandbox()
        result = await sandbox.execute(
            ["python", "-c", "import time; time.sleep(10)"],
            timeout_seconds=0.5,
        )
        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()

    def test_execution_result_defaults(self):
        r = ExecutionResult(exit_code=0)
        assert r.exit_code == 0
        assert r.stdout == ""
        assert r.stderr == ""
        assert r.duration_ms == 0.0


