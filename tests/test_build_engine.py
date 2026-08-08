"""Machine-native build engine — phase tracking and force nudges."""

from __future__ import annotations

from types import SimpleNamespace

from remedy.core.build_engine import (
    BuildTurnState,
    begin_build_turn,
    build_protocol_block,
    looks_like_build_request,
    monologue_block_nudge,
    next_machine_nudge,
    observe_tool_batch,
    should_force_tools_for_build,
)


def test_detects_build_requests():
    assert looks_like_build_request("implement a REST API for todos")
    assert looks_like_build_request("build me a CLI that greets")
    assert looks_like_build_request("fix the pytest failures in tests/")
    assert not looks_like_build_request("thanks")
    assert not looks_like_build_request("what is a monad?")


def test_begin_build_turn_stamps_runtime():
    rt = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _session_brief=None,
    )
    st = begin_build_turn(rt, "implement soul field tests")
    assert st is not None
    assert st.active
    assert rt._build_turn is st
    assert "SCOUT" in build_protocol_block(st) or "scout" in build_protocol_block(st).lower()


def test_serial_explore_forces_implement():
    st = BuildTurnState(active=True, max_serial_explore=2, require_verify_after_writes=2)
    for _ in range(2):
        observe_tool_batch(
            st,
            [
                {
                    "function": {
                        "name": "file_read",
                        "arguments": '{"path":"a.py"}',
                    }
                }
            ],
        )
    assert st.serial_explore_streak >= 2
    nudge = next_machine_nudge(st)
    assert nudge is not None
    assert "IMPLEMENT" in nudge["content"]
    # Second call does not re-emit
    assert next_machine_nudge(st) is None


def test_writes_force_verify():
    st = BuildTurnState(active=True, require_verify_after_writes=1)
    observe_tool_batch(
        st,
        [
            {
                "function": {
                    "name": "file_write",
                    "arguments": '{"path":"app.py","content":"x"}',
                }
            }
        ],
    )
    assert st.write_steps == 1
    assert st.phase == "implement"
    nudge = next_machine_nudge(st)
    assert nudge is not None
    assert "VERIFY" in nudge["content"]


def test_verify_green_marks_done():
    st = BuildTurnState(active=True)
    observe_tool_batch(
        st,
        [{"function": {"name": "bash_exec", "arguments": '{"command":"pytest -q"}'}}],
        [{"role": "tool", "content": "verify exit_code=0\n5 passed"}],
    )
    assert st.last_verify_ok is True
    assert st.phase == "done"


def test_monologue_block():
    st = BuildTurnState(active=True)
    n = monologue_block_nudge(st)
    assert n is not None
    assert "MONOLOGUE" in n["content"] or "tool_calls" in n["content"]
    assert monologue_block_nudge(st) is None  # once


def test_force_tools_for_build():
    rt = SimpleNamespace(
        _llm_provider="anthropic",
        _llm_model="claude-sonnet-4",
        _llm_base_url="",
        _build_turn=BuildTurnState(active=True),
    )
    assert should_force_tools_for_build(rt, "hi") is True
    rt2 = SimpleNamespace(
        _llm_provider="xai",
        _llm_model="grok-4",
        _llm_base_url="",
        _build_turn=None,
    )
    assert should_force_tools_for_build(rt2, "build a web scraper") is True
