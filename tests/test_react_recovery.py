"""Unit tests for soft-fail recovery + non-stream completion parse.

Covers review follow-ups:
  1) soft-fail force-answer rebuild state machine
  2) non-stream JSON (message.tool_calls) vs SSE delta-only parse
  3) TurnState isolation for fingerprint / evidence / mission gate
"""

from __future__ import annotations

from remedy.core.providers import OpenAIProvider
from remedy.core.react_stream import (
    StreamRoundState,
    apply_openai_completion_message,
    apply_openai_sse_chunk,
    want_sse_stream_parse,
)
from remedy.core.react_turn import TurnState, soft_api_recovery_action

# -- soft API recovery -------------------------------------------------------


def test_soft_fail_first_error_rebuilds_not_stops() -> None:
    assert (
        soft_api_recovery_action(
            force_answer_api_fail_once=False,
            force_answer_sticky=False,
            api_soft_failures=0,
            max_api_soft_failures=3,
        )
        == "force_answer_rebuild"
    )


def test_soft_fail_sticky_means_force_answer_already_tried() -> None:
    # After rebuild with tools cleared, sticky is set; next non-200 stops.
    assert (
        soft_api_recovery_action(
            force_answer_api_fail_once=False,
            force_answer_sticky=True,
            api_soft_failures=1,
            max_api_soft_failures=3,
        )
        == "stop"
    )


def test_soft_fail_api_fail_once_always_stops() -> None:
    assert (
        soft_api_recovery_action(
            force_answer_api_fail_once=True,
            force_answer_sticky=False,
            api_soft_failures=0,
            max_api_soft_failures=3,
        )
        == "stop"
    )


def test_soft_fail_exhausted_budget_stops() -> None:
    assert (
        soft_api_recovery_action(
            force_answer_api_fail_once=False,
            force_answer_sticky=False,
            api_soft_failures=3,
            max_api_soft_failures=3,
        )
        == "stop"
    )


def test_soft_fail_still_allows_second_budget_slot() -> None:
    # Without sticky, soft failures under the cap can still rebuild.
    assert (
        soft_api_recovery_action(
            force_answer_api_fail_once=False,
            force_answer_sticky=False,
            api_soft_failures=2,
            max_api_soft_failures=3,
        )
        == "force_answer_rebuild"
    )


# -- SSE vs non-stream parse -------------------------------------------------


def test_want_sse_false_when_stream_false() -> None:
    assert (
        want_sse_stream_parse(
            {"stream": False, "messages": []},
            use_openai_sse=True,
            content_type="application/json",
        )
        is False
    )


def test_want_sse_true_for_event_stream() -> None:
    assert (
        want_sse_stream_parse(
            {"stream": True},
            use_openai_sse=True,
            content_type="text/event-stream",
        )
        is True
    )


def test_want_sse_false_when_body_stream_false_even_if_adapter_sse() -> None:
    # Local tool rounds force stream=False while adapter still uses_openai_sse.
    assert (
        want_sse_stream_parse(
            {"stream": False},
            use_openai_sse=True,
            content_type="application/json",
        )
        is False
    )


def test_sse_chunk_ignores_message_tool_calls() -> None:
    """Document the bug class: delta-only parse drops non-stream message.tool_calls."""
    state = StreamRoundState()
    apply_openai_sse_chunk(
        state,
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "list_dir",
                                    "arguments": "{}",
                                },
                            }
                        ],
                    }
                }
            ]
        },
        stream_live=False,
    )
    assert state.tool_calls_list() == []


def test_completion_message_captures_tool_calls() -> None:
    state = StreamRoundState()
    apply_openai_completion_message(
        state,
        {
            "choices": [
                {
                    "finish_reason": "tool_calls",
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "c1",
                                "type": "function",
                                "function": {
                                    "name": "list_dir",
                                    "arguments": '{"path":"."}',
                                },
                            }
                        ],
                    },
                }
            ]
        },
    )
    tcs = state.tool_calls_list()
    assert len(tcs) == 1
    assert tcs[0]["function"]["name"] == "list_dir"
    assert tcs[0]["id"] == "c1"
    assert state.finish_reason == "tool_calls"


def test_completion_message_content_and_reasoning() -> None:
    state = StreamRoundState()
    apply_openai_completion_message(
        state,
        {
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "Hello",
                        "reasoning_content": "think",
                    },
                }
            ]
        },
        stream_live=True,
    )
    assert "".join(state.content_parts) == "Hello"
    assert "think" in "".join(state.reasoning_parts)
    assert state.produced_user_text is True


def test_extract_response_also_reads_message_tool_calls() -> None:
    """Adapter path used by agent_react_loop non-SSE branch."""
    p = OpenAIProvider()
    parsed = p.extract_response(
        {
            "choices": [
                {
                    "message": {
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "t9",
                                "type": "function",
                                "function": {
                                    "name": "file_read",
                                    "arguments": '{"path":"a.py"}',
                                },
                            }
                        ],
                    }
                }
            ]
        }
    )
    assert parsed["tool_calls"] is not None
    assert parsed["tool_calls"][0]["function"]["name"] == "file_read"


# -- TurnState isolation -----------------------------------------------------


def test_turn_state_fingerprint_loop_isolated() -> None:
    a = TurnState(session_id="s1")
    b = TurnState(session_id="s2")
    assert a.note_fingerprint_loop() == 1
    assert a.note_fingerprint_loop() == 2
    assert b.note_fingerprint_loop() == 1
    assert a.fingerprint_loop_hits == 2
    assert b.fingerprint_loop_hits == 1


def test_turn_state_evidence_and_mission_defaults() -> None:
    t = TurnState(session_id="s")
    assert t.evidence_inject_eu == -1
    assert t.mission_gate_nudge_done is False
    t.evidence_inject_eu = 4
    t.mission_gate_nudge_done = True
    other = TurnState(session_id="s")
    assert other.evidence_inject_eu == -1
    assert other.mission_gate_nudge_done is False
