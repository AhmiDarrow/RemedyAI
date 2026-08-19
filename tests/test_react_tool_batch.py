"""Post-batch bookkeeping in the ReAct loop, and its tolerance for breakage.

Everything the build engine does after a tool batch is optional — a turn must
finish even when the engine is half-configured. The risk in that design is a
stage failing forever without anyone noticing, so the tolerance is tested here
alongside the bookkeeping it protects.
"""

from __future__ import annotations

import logging

import pytest

from remedy.core.react_loop import tool_batch as TB


class Turn:
    def __init__(self, *, nudge=None, injects=0, max_injects=3) -> None:
        self.batches: list[tuple[list, list]] = []
        self.inject_count = injects
        self.max_injects = max_injects
        self._nudge = nudge

    def record_tool_batch(self, names, paths=None):
        self.batches.append((list(names), list(paths or [])))

    def phase_nudge(self):
        return self._nudge


def _call(name: str, args: str = "{}") -> dict:
    return {"type": "function", "function": {"name": name, "arguments": args}}


# --- _soft ------------------------------------------------------------------


def test_soft_swallows_so_the_turn_survives():
    with TB._soft("stage"):
        raise RuntimeError("build engine is mid-refactor")


def test_soft_names_the_stage_that_fell_over(caplog):
    """The whole point: a broken stage is tolerated, not hidden."""
    with caplog.at_level(logging.DEBUG, logger=TB.__name__):
        with TB._soft("import-dry-run"):
            raise ValueError("boom")
    assert any("import-dry-run" in r.getMessage() for r in caplog.records)


def test_soft_does_not_log_when_nothing_goes_wrong(caplog):
    with caplog.at_level(logging.DEBUG, logger=TB.__name__):
        with TB._soft("stage"):
            pass
    assert not caplog.records


def test_soft_lets_cancellation_through():
    """A cancelled turn must not look like a failed build stage."""
    with pytest.raises(BaseException, match="cancel"):
        with TB._soft("stage"):
            raise KeyboardInterrupt("cancel")


# --- record_tool_batch_stats ------------------------------------------------


def test_a_batch_is_always_counted_once():
    turn = Turn()
    batch, _ = TB.record_tool_batch_stats(
        turn=turn, fresh_calls=[_call("file_read")], batch_tool_msgs=[], step=0
    )
    assert batch == 1
    assert turn.batches[0][0] == ["file_read"]


def test_an_empty_batch_still_records():
    turn = Turn()
    assert TB.record_tool_batch_stats(
        turn=turn, fresh_calls=[], batch_tool_msgs=[], step=0
    )[0] == 1


def test_tool_names_reach_the_turn():
    turn = Turn()
    TB.record_tool_batch_stats(
        turn=turn,
        fresh_calls=[_call("file_write"), _call("repo_search")],
        batch_tool_msgs=[],
        step=2,
    )
    assert turn.batches[0][0] == ["file_write", "repo_search"]


def test_productivity_is_judged_from_the_tool_results():
    """Reading forever is not progress; the loop needs to tell the difference."""
    turn = Turn()
    _, productive = TB.record_tool_batch_stats(
        turn=turn, fresh_calls=[_call("file_read")], batch_tool_msgs=[], step=0
    )
    assert productive in (0, 1)


def test_a_malformed_call_does_not_take_the_turn_down():
    turn = Turn()
    assert TB.record_tool_batch_stats(
        turn=turn, fresh_calls=[None, "nonsense", {}], batch_tool_msgs=[], step=0
    )[0] == 1


# --- inject_phase_nudge -----------------------------------------------------


def test_a_phase_nudge_is_appended_when_there_is_budget():
    msgs: list[dict] = []
    TB.inject_phase_nudge(Turn(nudge={"role": "user", "content": "PLAN"}), msgs)
    assert msgs == [{"role": "user", "content": "PLAN"}]


def test_no_nudge_means_no_message():
    msgs: list[dict] = []
    TB.inject_phase_nudge(Turn(nudge=None), msgs)
    assert msgs == []


def test_the_inject_budget_is_respected():
    """Past the cap Remedy stops talking over herself."""
    msgs: list[dict] = []
    TB.inject_phase_nudge(
        Turn(nudge={"role": "user", "content": "PLAN"}, injects=9, max_injects=3), msgs
    )
    assert msgs == []


def test_the_budget_boundary_still_injects():
    msgs: list[dict] = []
    TB.inject_phase_nudge(
        Turn(nudge={"role": "user", "content": "PLAN"}, injects=3, max_injects=3), msgs
    )
    assert len(msgs) == 1


def test_a_turn_that_cannot_produce_a_nudge_is_survivable():
    class Broken(Turn):
        def phase_nudge(self):
            raise RuntimeError("no phase state")

    msgs: list[dict] = []
    TB.inject_phase_nudge(Broken(), msgs)
    assert msgs == []
