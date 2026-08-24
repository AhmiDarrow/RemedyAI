"""A green build verify does not mean the owner's request is finished.

After a write, the build engine runs its own smoke test. When that passes it
injects ``[Build engine · GREEN · stop building]``, and the local body
optimizer responded by stripping every tool and capping the reply to a short
summary.

For "create fib.py, **then run it and tell me its output**" that is the wrong
call: the smoke test (``tests/test_remedy_build_smoke.py``) has nothing to do
with fib.py, and the second half of the request had not happened yet. Captured
live at step 1, immediately after the write::

    step=1  n_tools=0  max_tok=341
    [Build engine · AUTO VERIFY · GREEN] Machine ran: `pytest -q tests/...`
    [Build engine · GREEN · stop building] Reply with a short user summary only
    Verify is GREEN. Reply with at most 6 short lines. Then stop.

The model never declined to run the file — it was forbidden to. Every model
tested failed this identically, which is what identified it as a harness bug.

Tools are now held through GREEN while an explicitly requested execution is
still outstanding, and released as soon as it has happened.
"""

from __future__ import annotations

from typing import Any

import pytest

from remedy.core.local_agent_optimize import (
    apply_local_body_optimize,
    execution_already_ran,
    request_wants_execution,
)

LOCAL = {
    "provider": "rmb",
    "model": "qwen3-14b",
    "base_url": "http://127.0.0.1:8787/v1",
}

GREEN = {
    "role": "user",
    "content": (
        "[Build engine · GREEN · stop building]\n"
        "Machine verify passed: `pytest -q tests/test_remedy_build_smoke.py`.\n"
        "Reply with a **short** user summary only (<=6 lines)."
    ),
}

RUN_REQUEST = (
    "Create fib.py that prints the first 10 Fibonacci numbers, "
    "then run it and tell me its output."
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
        for n in ("file_write", "file_read", "bash_exec", "run_python_file")
    ]


def _call(tool: str) -> dict[str, Any]:
    return {
        "role": "assistant",
        "tool_calls": [{"function": {"name": tool, "arguments": "{}"}}],
    }


def _body() -> dict[str, Any]:
    return {
        "model": "m",
        "messages": [],
        "tools": _tools(),
        "tool_choice": "auto",
        "max_tokens": 2048,
    }


class TestRequestWantsExecution:
    @pytest.mark.parametrize(
        "msg",
        [
            RUN_REQUEST,
            "write hello.py and run it",
            "fix broken.py then run it again to prove it works",
            "build the script and tell me the output",
            "create main.py and execute it",
        ],
    )
    def test_detects_explicit_run_requests(self, msg: str) -> None:
        assert request_wants_execution(msg)

    @pytest.mark.parametrize(
        "msg",
        [
            "Create a file named hello.py that prints Hello, Remedy!",
            "read config.py and tell me the port",
            "list the files in the project",
            "",
        ],
    )
    def test_ignores_requests_with_no_run_step(self, msg: str) -> None:
        assert not request_wants_execution(msg)


class TestExecutionAlreadyRan:
    def test_false_after_only_a_write(self) -> None:
        assert not execution_already_ran([_call("file_write")])

    @pytest.mark.parametrize("tool", ["bash_exec", "host_run", "run_python_file"])
    def test_true_after_an_execution_tool(self, tool: str) -> None:
        assert execution_already_ran([_call("file_write"), _call(tool)])

    def test_empty_history(self) -> None:
        assert not execution_already_ran(None)
        assert not execution_already_ran([])


class TestGreenDoesNotStripPendingRun:
    def test_tools_survive_green_when_the_run_is_still_owed(self) -> None:
        """The regression: GREEN must not end a turn mid-request."""
        history = [
            {"role": "user", "content": RUN_REQUEST},
            _call("file_write"),
            GREEN,
        ]
        out = apply_local_body_optimize(
            _body(),
            user_message=RUN_REQUEST,
            step_index=1,
            history=history,
            **LOCAL,
        )
        assert out.get("tools"), "tools were stripped before the run happened"
        assert int(out.get("max_tokens") or 0) > 512

    def test_green_still_finalizes_once_the_run_has_happened(self) -> None:
        history = [
            {"role": "user", "content": RUN_REQUEST},
            _call("file_write"),
            _call("run_python_file"),
            GREEN,
        ]
        out = apply_local_body_optimize(
            _body(),
            user_message=RUN_REQUEST,
            step_index=2,
            history=history,
            **LOCAL,
        )
        assert not out.get("tools"), "GREEN should finalize once the run is done"

    def test_green_still_finalizes_when_no_run_was_asked_for(self) -> None:
        msg = "Create a file named hello.py that prints Hello, Remedy!"
        history = [{"role": "user", "content": msg}, _call("file_write"), GREEN]
        out = apply_local_body_optimize(
            _body(), user_message=msg, step_index=1, history=history, **LOCAL
        )
        assert not out.get("tools")


class TestKeepAgencyAfterGreen:
    """The loop-side half: green must not empty `tools` mid-request."""

    def test_pending_run_keeps_agency_even_with_no_build_state(self) -> None:
        from remedy.core.build_engine import keep_agency_after_green

        assert keep_agency_after_green(None, RUN_REQUEST, run_already_done=False)

    def test_completed_run_releases_agency(self) -> None:
        from remedy.core.build_engine import keep_agency_after_green

        assert not keep_agency_after_green(None, RUN_REQUEST, run_already_done=True)

    def test_no_run_requested_releases_agency(self) -> None:
        from remedy.core.build_engine import keep_agency_after_green

        assert not keep_agency_after_green(
            None, "Create hello.py that prints hi", run_already_done=False
        )

    def test_default_keeps_the_old_signature_working(self) -> None:
        from remedy.core.build_engine import keep_agency_after_green

        assert not keep_agency_after_green(None, "list the files")
