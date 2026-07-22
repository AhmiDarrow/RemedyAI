"""Tool execution runtime — the unified pipeline for running tools.

Orchestrates the full lifecycle: policy check → validation → execution →
result processing → provenance recording. Supports subprocess, Docker,
and MCP-based tool backends with retry, timeout, and streaming.
"""

from __future__ import annotations

import asyncio
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable, Optional
from uuid import UUID, uuid4

from remedy.execution.policy import ExecutionPolicy, PolicyDecision
from remedy.models import ToolCall, ToolDefinition, ToolResult, ToolSource


@dataclass
class ToolContext:
    session_id: Optional[str] = None
    user_id: Optional[str] = None
    channel: Optional[str] = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionRecord:
    call_id: UUID
    tool_name: str
    source: str
    started_at: float
    ended_at: float = 0.0
    exit_code: int = 0
    success: bool = False
    stdout: str = ""
    stderr: str = ""
    duration_ms: float = 0.0
    retries: int = 0
    policy_decision: Optional[PolicyDecision] = None
    context: Optional[ToolContext] = None


class ToolRuntime:
    """Unified runtime for executing tools across all backends.

    Pipeline:
      1. Permission check (policy engine)
      2. Pre-execution validation
      3. Execute with timeout
      4. Retry on failure (if configured)
      5. Post-execution processing
      6. Record provenance
    """

    def __init__(
        self,
        sandbox=None,  # Sandbox
        policy: Optional[ExecutionPolicy] = None,
        tool_registry=None,  # ToolRegistry
        max_retries: int = 3,
        retry_backoff: float = 1.0,
        default_timeout: float = 30.0,
    ) -> None:
        self.sandbox = sandbox
        self.policy = policy or ExecutionPolicy()
        self.tool_registry = tool_registry
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.default_timeout = default_timeout

        self._handlers: dict[str, Callable] = {}  # tool_name → handler
        self._history: list[ExecutionRecord] = []

    # -- handler registration -------------------------------------------------

    def register_handler(self, tool_name: str, handler: Callable) -> None:
        """Register a direct handler for a tool (bypasses sandbox)."""
        self._handlers[tool_name] = handler

    # -- execution pipeline ---------------------------------------------------

    async def execute(
        self,
        tool_call: ToolCall,
        context: Optional[ToolContext] = None,
        timeout: Optional[float] = None,
    ) -> ToolResult:
        """Run a tool through the full pipeline and return a result."""
        ctx = context or ToolContext()
        t0 = time.monotonic()

        record = ExecutionRecord(
            call_id=tool_call.id,
            tool_name=tool_call.tool_name,
            source=tool_call.source.value if tool_call.source else "unknown",
            started_at=t0,
            context=ctx,
        )

        # 1. Permission check
        decision = self.policy.evaluate(tool_call.tool_name)
        record.policy_decision = decision

        if not decision.allowed:
            record.ended_at = time.monotonic()
            record.success = False
            record.stderr = decision.reason
            self._history.append(record)
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=f"Policy denied: {decision.reason}",
            )

        if decision.requires_approval and not tool_call.approved:
            record.ended_at = time.monotonic()
            record.success = False
            record.stderr = "Requires approval"
            self._history.append(record)
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error="Tool requires approval",
                data={"requires_approval": True},
            )

        # 2. Execute with retry
        last_exception = None
        result = None

        for attempt in range(self.max_retries + 1):
            record.retries = attempt
            try:
                result = await self._do_execute(tool_call, timeout or self.default_timeout)
                if result.success:
                    break
            except Exception as e:
                last_exception = e
                record.stderr = str(e)

            if attempt < self.max_retries:
                wait = self.retry_backoff * (2 ** attempt)
                await asyncio.sleep(wait)

        # Fallback result
        if result is None:
            result = ToolResult(
                call_id=tool_call.id,
                success=False,
                error=str(last_exception) if last_exception else "Unknown error",
            )

        # 3. Record
        record.ended_at = time.monotonic()
        record.duration_ms = (record.ended_at - record.started_at) * 1000
        record.success = result.success
        record.exit_code = result.exit_code if hasattr(result, "exit_code") else (0 if result.success else 1)

        self._history.append(record)

        # 4. Update registry stats
        if self.tool_registry:
            try:
                self.tool_registry.record_invocation(tool_call, result)
            except Exception:
                pass

        return result

    async def _do_execute(self, tool_call: ToolCall, timeout: float) -> ToolResult:
        """Internal execution dispatcher."""

        # Direct handler (fast path)
        if tool_call.tool_name in self._handlers:
            try:
                handler = self._handlers[tool_call.tool_name]
                data = await handler(tool_call.arguments)
                return ToolResult(
                    call_id=tool_call.id,
                    success=True,
                    data={"result": str(data)},
                )
            except Exception as e:
                return ToolResult(
                    call_id=tool_call.id,
                    success=False,
                    error=str(e),
                )

        # Sandbox execution
        if self.sandbox:
            return await self._execute_sandbox(tool_call, timeout)

        return ToolResult(
            call_id=tool_call.id,
            success=False,
            error=f"No handler or sandbox for tool: {tool_call.tool_name}",
        )

    async def _execute_sandbox(self, tool_call: ToolCall, timeout: float) -> ToolResult:
        """Execute via the sandbox backend."""
        try:
            command = self._build_command(tool_call)
            exec_result = await self.sandbox.execute(
                command=command,
                timeout_seconds=timeout,
                workdir=getattr(self.sandbox, "_workdir", None),
            )

            return ToolResult(
                call_id=tool_call.id,
                success=exec_result.exit_code == 0,
                data={
                    "stdout": exec_result.stdout,
                    "stderr": exec_result.stderr,
                    "exit_code": exec_result.exit_code,
                },
                duration_ms=exec_result.duration_ms,
                error=exec_result.stderr if exec_result.exit_code != 0 else None,
            )
        except Exception as e:
            return ToolResult(
                call_id=tool_call.id,
                success=False,
                error=str(e),
            )

    def _build_command(self, tool_call: ToolCall) -> list[str]:
        """Build a command list from a tool call."""
        if tool_call.tool_name == "bash_exec":
            return ["pwsh", "-NoProfile", "-Command", tool_call.arguments.get("command", "")]
        if tool_call.tool_name.startswith("python_"):
            code = tool_call.arguments.get("code", tool_call.arguments.get("command", ""))
            return ["python", "-c", code]
        return [tool_call.tool_name] + [str(v) for v in tool_call.arguments.values()]

    # -- provenance ----------------------------------------------------------

    def get_history(
        self,
        tool_name: Optional[str] = None,
        limit: int = 50,
    ) -> list[ExecutionRecord]:
        records = self._history
        if tool_name:
            records = [r for r in records if r.tool_name == tool_name]
        return sorted(records, key=lambda r: r.started_at, reverse=True)[:limit]

    def get_stats(self) -> dict[str, Any]:
        total = len(self._history)
        if total == 0:
            return {"total_calls": 0}

        success = sum(1 for r in self._history if r.success)
        tool_counts: dict[str, int] = {}
        for r in self._history:
            tool_counts[r.tool_name] = tool_counts.get(r.tool_name, 0) + 1

        durations = [r.duration_ms for r in self._history if r.duration_ms > 0]

        return {
            "total_calls": total,
            "success_count": success,
            "failure_count": total - success,
            "success_rate": success / total,
            "avg_duration_ms": sum(durations) / len(durations) if durations else 0,
            "by_tool": dict(sorted(tool_counts.items(), key=lambda x: -x[1])[:10]),
        }

    def clear_history(self) -> None:
        self._history.clear()
