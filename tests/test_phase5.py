"""Phase 5 tests: Execution sandbox, tool runtime, and policy engine."""

from __future__ import annotations

import json

import pytest

from remedy.execution.docker import DockerSandbox
from remedy.execution.policy import (
    ExecutionPolicy,
    PolicyAction,
    PolicyRule,
    default_policy,
)
from remedy.execution.runtime import ToolContext, ToolRuntime
from remedy.execution.sandbox import ExecutionResult, SubprocessSandbox
from remedy.models import ToolCall, ToolSource


class TestExecutionPolicy:
    """Policy engine tests."""

    def test_default_allow(self):
        policy = ExecutionPolicy(default_action=PolicyAction.ALLOW)
        decision = policy.evaluate("any_tool")
        assert decision.allowed
        assert "Allowed by default" in decision.reason

    def test_default_deny(self):
        policy = ExecutionPolicy(default_action=PolicyAction.DENY)
        decision = policy.evaluate("any_tool")
        assert not decision.allowed

    def test_explicit_allow(self):
        policy = ExecutionPolicy(default_action=PolicyAction.DENY)
        policy.allow("bash_exec", "Safe command")
        assert policy.evaluate("bash_exec").allowed
        assert not policy.evaluate("other_tool").allowed

    def test_deny_takes_precedence(self):
        policy = ExecutionPolicy()
        policy.allow("bash_exec")
        policy.deny("bash_exec", "Blocked for safety")
        decision = policy.evaluate("bash_exec")
        assert not decision.allowed
        assert "Blocked for safety" in decision.reason

    def test_wildcard_patterns(self):
        policy = ExecutionPolicy(default_action=PolicyAction.DENY)
        policy.allow("memory_*")
        policy.allow("skill_*")

        assert policy.evaluate("memory_search").allowed
        assert policy.evaluate("memory_add").allowed
        assert policy.evaluate("skill_load").allowed
        assert not policy.evaluate("bash_exec").allowed

    def test_exact_match_vs_wildcard(self):
        policy = ExecutionPolicy()
        policy.allow("memory_*")
        policy.deny("memory_delete")

        assert policy.evaluate("memory_search").allowed
        assert not policy.evaluate("memory_delete").allowed

    def test_star_wildcard(self):
        policy = ExecutionPolicy(default_action=PolicyAction.DENY)
        policy.allow("*")
        assert policy.evaluate("any_tool").allowed
        assert policy.evaluate("another_tool").allowed

    def test_require_approval(self):
        policy = ExecutionPolicy(default_action=PolicyAction.ALLOW)
        policy.require_approval("bash_exec")

        decision = policy.evaluate("bash_exec")
        assert decision.allowed
        assert decision.requires_approval

    def test_check_shortcut(self):
        policy = ExecutionPolicy()
        policy.deny("dangerous_tool")
        assert policy.check("safe_tool")
        assert not policy.check("dangerous_tool")

    def test_default_policy_safety(self):
        policy = default_policy()
        assert policy.check("memory_search")
        assert not policy.check("sudo_kill")
        assert not policy.check("raw_sql_exec")

    def test_rules_for_tool(self):
        policy = ExecutionPolicy()
        policy.allow("test_tool")
        policy.deny("test_tool", "override")
        rules = policy.rules_for("test_tool")
        assert len(rules) == 2

    def test_clear_rules_by_scope(self):
        policy = ExecutionPolicy()
        policy.allow("tool_a", scope="session")
        policy.deny("tool_b", scope="global")
        policy.clear_rules(scope="session")
        assert policy.rule_count == 1

    def test_denied_allowed_sets(self):
        policy = ExecutionPolicy()
        policy.allow("safe1")
        policy.allow("safe2")
        policy.deny("danger1")
        assert "safe1" in policy.allowed_tools()
        assert "danger1" in policy.denied_tools()

    def test_add_rule_from_dataclass(self):
        policy = ExecutionPolicy()
        policy.add_rule(PolicyRule(tool_name="blocked", action=PolicyAction.DENY, reason="test"))
        assert not policy.check("blocked")


class TestToolRuntime:
    """Tool runtime execution pipeline tests."""

    @pytest.mark.asyncio
    async def test_handler_execution(self):
        runtime = ToolRuntime()

        async def my_handler(args):
            return f"Handled: {args.get('key')}"

        runtime.register_handler("test_handler", my_handler)

        call = ToolCall(tool_name="test_handler", arguments={"key": "value"})
        result = await runtime.execute(call)

        assert result.success
        assert "Handled: value" in json.dumps(result.data)

    @pytest.mark.asyncio
    async def test_policy_denies(self):
        policy = ExecutionPolicy()
        policy.deny("blocked_tool")

        runtime = ToolRuntime(policy=policy)
        call = ToolCall(tool_name="blocked_tool", arguments={})
        result = await runtime.execute(call)

        assert not result.success
        assert "Policy denied" in result.error

    @pytest.mark.asyncio
    async def test_requires_approval(self):
        policy = ExecutionPolicy()
        policy.require_approval("needs_ok")

        async def handler(args):
            return "done"

        runtime = ToolRuntime(policy=policy)
        runtime.register_handler("needs_ok", handler)

        call = ToolCall(tool_name="needs_ok", arguments={})
        result = await runtime.execute(call)

        assert not result.success
        assert "approval" in result.error.lower()
        assert result.data["requires_approval"] is True

    @pytest.mark.asyncio
    async def test_provenance_recording(self):
        async def handler(args):
            return "ok"

        runtime = ToolRuntime()
        runtime.register_handler("test", handler)

        call = ToolCall(tool_name="test", arguments={})
        await runtime.execute(call)

        history = runtime.get_history()
        assert len(history) == 1
        assert history[0].tool_name == "test"
        assert history[0].success

    @pytest.mark.asyncio
    async def test_get_stats(self):
        async def handler(args):
            return "ok"

        runtime = ToolRuntime()
        runtime.register_handler("tool_a", handler)
        runtime.register_handler("tool_b", handler)

        await runtime.execute(ToolCall(tool_name="tool_a", arguments={}))
        await runtime.execute(ToolCall(tool_name="tool_b", arguments={}))
        await runtime.execute(ToolCall(tool_name="tool_a", arguments={}))

        stats = runtime.get_stats()
        assert stats["total_calls"] == 3
        assert stats["success_count"] == 3
        assert stats["by_tool"]["tool_a"] == 2

    @pytest.mark.asyncio
    async def test_filter_history_by_tool(self):
        async def handler(args):
            return "ok"

        runtime = ToolRuntime()
        runtime.register_handler("alpha", handler)
        runtime.register_handler("beta", handler)

        await runtime.execute(ToolCall(tool_name="alpha", arguments={}))
        await runtime.execute(ToolCall(tool_name="beta", arguments={}))

        alpha_hist = runtime.get_history(tool_name="alpha")
        assert len(alpha_hist) == 1
        assert alpha_hist[0].tool_name == "alpha"

    @pytest.mark.asyncio
    async def test_clear_history(self):
        async def handler(args):
            return "ok"

        runtime = ToolRuntime()
        runtime.register_handler("test", handler)

        await runtime.execute(ToolCall(tool_name="test", arguments={}))
        runtime.clear_history()
        assert len(runtime.get_history()) == 0


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


class TestDockerSandbox:
    """Docker sandbox tests (integration with real Docker)."""

    def test_availability_check(self):
        docker = DockerSandbox()
        result = docker.available
        assert isinstance(result, bool)

    @pytest.mark.asyncio
    async def test_not_available_message(self):
        docker = DockerSandbox()
        docker._available = False
        result = await docker.execute(["echo", "hello"])
        assert result.exit_code == -1
        assert "not available" in result.stderr.lower()

    def test_default_configuration(self):
        docker = DockerSandbox()
        assert docker.image == "python:3.12-slim"
        assert docker.network == "none"
        assert docker.memory_limit == "256m"


class TestToolContext:
    """Tool execution context tests."""

    def test_default_context(self):
        ctx = ToolContext()
        assert ctx.session_id is None
        assert ctx.user_id is None
        assert ctx.metadata == {}

    def test_full_context(self):
        ctx = ToolContext(
            session_id="sess-1",
            user_id="user-1",
            channel="cli",
            metadata={"env": "test"},
        )
        assert ctx.session_id == "sess-1"
        assert ctx.user_id == "user-1"
        assert ctx.channel == "cli"
        assert ctx.metadata["env"] == "test"

    def test_metadata_field(self):
        ctx = ToolContext(metadata={"key": "value", "number": 42})
        assert ctx.metadata["key"] == "value"
        assert ctx.metadata["number"] == 42


class TestIntegration:
    """Runtime + policy + sandbox integration."""

    @pytest.mark.asyncio
    async def test_end_to_end_approved(self):
        sandbox = SubprocessSandbox()
        policy = ExecutionPolicy()
        runtime = ToolRuntime(sandbox=sandbox, policy=policy)

        # Register bash_exec as a handler pointing to sandbox
        call = ToolCall(
            tool_name="python_test",
            arguments={"code": "print('integration works')"},
            source=ToolSource.BUILTIN,
        )

        # Wire up the handler
        async def handler(args):
            result = await sandbox.execute(
                ["python", "-c", args.get("code", "pass")],
                timeout_seconds=5.0,
            )
            return f"stdout={result.stdout} stderr={result.stderr}"

        runtime.register_handler("python_test", handler)

        result = await runtime.execute(call)
        assert result.success
        assert "integration works" in str(result.data)

    @pytest.mark.asyncio
    async def test_retry_on_failure(self):
        calls = []

        async def flaky(args):
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("transient failure")
            return "finally"

        runtime = ToolRuntime(max_retries=3, retry_backoff=0.01)
        runtime.register_handler("flaky", flaky)

        result = await runtime.execute(ToolCall(tool_name="flaky", arguments={}))
        assert result.success
        assert len(calls) == 3
        assert runtime.get_history()[0].retries == 2
