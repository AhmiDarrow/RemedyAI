"""Execution tools come back as soon as the model has written something.

``filter_tools_write_first`` withholds ``bash_exec`` / ``host_run`` /
``run_python_file`` on early implement steps so a local model writes code
instead of shelling around. The intent is *write before you run*, but the gate
was ``step_index > 1`` — a pure step count. A model that wrote its file on step
0 still had no way to execute it on step 1, so "create fib.py, run it, and tell
me the output" ended at the write.

That failure reproduced on three unrelated models — a local base model, a local
fine-tune, and a cloud model — which is what identified it as a harness bug
rather than a model limitation. Every one of them showed the same trace:
``tools=['file_write']`` and then a final answer.

The gate is now "has a write actually happened", which satisfies the original
intent without stranding the next step.
"""

from __future__ import annotations

from typing import Any

from remedy.core.local_agent_optimize import (
    filter_tools_write_first,
    write_already_attempted,
)

IMPLEMENT = "Create fib.py that prints the first 10 Fibonacci numbers, then run it."
EXEC_TOOLS = ("bash_exec", "host_run", "run_python_file")
NAMES = (
    "list_dir",
    "file_read",
    "file_write",
    "file_edit",
    "repo_search",
    "bash_exec",
    "host_run",
    "run_python_file",
)


def _tools() -> list[dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": n,
                "description": n,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for n in NAMES
    ]


def _names(tools: list[dict[str, Any]] | None) -> list[str]:
    return [t["function"]["name"] for t in (tools or [])]


def _has_exec(tools: list[dict[str, Any]] | None) -> bool:
    got = set(_names(tools))
    return any(n in got for n in EXEC_TOOLS)


def _after_write(tool: str = "file_write") -> list[dict[str, Any]]:
    return [
        {"role": "user", "content": IMPLEMENT},
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": tool, "arguments": "{}"}}],
        },
        {"role": "tool", "content": "Wrote 120 bytes to fib.py"},
    ]


class TestWriteAlreadyAttempted:
    def test_detects_each_write_tool(self) -> None:
        for tool in ("file_write", "file_edit", "file_edit_batch", "apply_patch"):
            assert write_already_attempted(_after_write(tool)), tool

    def test_read_only_history_is_not_a_write(self) -> None:
        history = [
            {"role": "user", "content": IMPLEMENT},
            {
                "role": "assistant",
                "tool_calls": [{"function": {"name": "file_read", "arguments": "{}"}}],
            },
        ]
        assert not write_already_attempted(history)

    def test_empty_and_malformed_history(self) -> None:
        assert not write_already_attempted(None)
        assert not write_already_attempted([])
        assert not write_already_attempted([{"role": "assistant"}, "junk", None])


class TestWriteFirstFilter:
    def test_exec_withheld_before_any_write(self) -> None:
        out = filter_tools_write_first(_tools(), user_message=IMPLEMENT, step_index=0)
        assert not _has_exec(out)

    def test_exec_still_withheld_on_step_one_without_a_write(self) -> None:
        out = filter_tools_write_first(_tools(), user_message=IMPLEMENT, step_index=1)
        assert not _has_exec(out)

    def test_exec_returns_immediately_after_a_write(self) -> None:
        """The regression: step 1 with a completed write must allow running it."""
        out = filter_tools_write_first(
            _tools(),
            user_message=IMPLEMENT,
            step_index=1,
            history=_after_write(),
        )
        assert _has_exec(out)

    def test_write_tools_are_never_removed(self) -> None:
        out = filter_tools_write_first(_tools(), user_message=IMPLEMENT, step_index=0)
        assert "file_write" in _names(out)

    def test_later_steps_are_unfiltered_as_before(self) -> None:
        out = filter_tools_write_first(_tools(), user_message=IMPLEMENT, step_index=2)
        assert _has_exec(out)

    def test_non_implement_messages_are_untouched(self) -> None:
        out = filter_tools_write_first(
            _tools(), user_message="what is the capital of France?", step_index=0
        )
        assert _has_exec(out)
