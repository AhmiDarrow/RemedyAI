"""A turn that stops making progress must stop calling the model.

``max_total`` defaults to 10_000 ReAct steps — a safety net for long
*productive* builds, not for a stuck turn. When a turn runs no tools and adds
no messages, every round re-sends a byte-identical request, so nothing
downstream can break the cycle: the inputs never change.

Observed live on a local host: 226 consecutive rounds on one turn, message list
frozen at 10 entries, zero tool calls. On a local model that is minutes of GPU
for nothing; against a cloud provider it is the same count of billed calls.

The brake watches tool activity only. The threshold sits well above every
legitimate no-tool sequence the loop steers on purpose (the zero-tool drive
allows 8 attempts, stale epochs another 8), so it is a runaway backstop rather
than a policy knob. It finalizes through the normal last-step path so the user
still gets a real answer rather than silence.
"""

from __future__ import annotations

import inspect

from remedy.core.react_loop import loop as loop_mod
from remedy.core.react_loop import loop_finals as finals_mod
from remedy.core.react_loop import loop_http as http_mod
from remedy.core.react_loop import loop_prelude as prelude_mod
from remedy.core.react_loop import loop_round as round_mod
from remedy.core.react_loop import loop_steps as steps_mod


def _source() -> str:
    """Orchestrator + extracted step modules (split so mypy can type each)."""
    return "\n".join(
        inspect.getsource(m)
        for m in (loop_mod, steps_mod, prelude_mod, http_mod, round_mod, finals_mod)
    )


def test_brake_state_is_initialised_before_the_step_loop() -> None:
    src = _source()
    init = src.index("no_progress_steps = 0")
    loop_start = src.index("for step in range(max_total):")
    assert init < loop_start, "brake state must exist before the loop starts"
    assert "stalled_finalize = False" in src
    assert "max_no_progress_steps = 25" in src


def test_fingerprint_is_tool_activity_only() -> None:
    """Message growth must NOT count as progress.

    The loop answers a tool-less round by appending a nudge. If the brake
    watched message count, that nudge would read as progress and reset the
    counter every round — so a nudge-loop spins indefinitely (535s on a single
    turn was observed before this was narrowed to tool activity).
    """
    src = _source()
    start = src.index("for step in range(max_total):")
    block = src[start : start + 1600]
    fp = block[block.index("_fp = (") : block.index("if _fp ==")]
    assert "tools_executed_this_turn" in fp
    assert "tool_batches_this_turn" in fp
    assert "len(messages)" not in fp, "message growth is not progress"


def test_stall_triggers_finalize_not_a_bare_break() -> None:
    """Breaking out could end the turn silently; finalizing yields an answer."""
    src = _source()
    start = src.index("for step in range(max_total):")
    block = src[start : start + 1600]
    assert "stalled_finalize = True" in block
    assert "no_progress_steps >= max_no_progress_steps" in block


def test_final_step_honours_the_stall() -> None:
    src = _source()
    assert "is_final_step = stalled_finalize or step >= max_total - 1" in src


def test_zero_tool_drive_cannot_undo_the_stall() -> None:
    """The drive resets is_final_step; it must not reopen a stalled turn."""
    src = _source()
    anchor = src.index(
        "# Work request + zero tool evidence is never a successful final."
    )
    gate = src[anchor : anchor + 400]
    assert "not stalled_finalize" in gate


def test_progress_resets_the_counter() -> None:
    src = _source()
    start = src.index("for step in range(max_total):")
    block = src[start : start + 1600]
    tail = block[block.index("else:") :]
    assert "no_progress_steps = 0" in tail


def test_all_error_batch_brake_exists() -> None:
    """Failing tool calls are activity, so the fingerprint cannot see them.

    A model that varies its args slightly each round also slips fingerprint
    dedup. Observed: 44 consecutive file_edit calls all failing "old_string and
    new_string are identical", and 42 all failing TOOL_ARGS_TRUNCATED. Nothing
    changed on disk across either run.
    """
    src = _source()
    assert "all_error_batches = 0" in src
    assert "max_all_error_batches = 8" in src
    anchor = src.index("all_error_batches += 1")
    # Wide enough to survive neighbouring edits; the marker sits in the same
    # batch-inspection block, above the counter.
    block = src[anchor - 1600 : anchor + 800]
    assert "Error [" in block, "must detect the tool error marker"
    assert "stalled_finalize = True" in block
    # A batch with any success must reset the counter.
    assert "all_error_batches = 0" in src[anchor : anchor + 900]


def test_cumulative_failed_tool_ceiling() -> None:
    """Requiring a batch to be *entirely* errors was too lenient.

    One turn ran 1205 tool calls with 1062 failures (88%): the occasional
    success reset the consecutive-batch counter every few rounds, so the
    all-error brake never fired. A cumulative ceiling cannot be reset that way.
    """
    src = _source()
    assert "failed_tools_this_turn = 0" in src
    assert "max_failed_tools = 60" in src
    anchor = src.index("failed_tools_this_turn >= max_failed_tools")
    block = src[anchor - 400 : anchor + 400]
    assert "stalled_finalize = True" in block


def test_truncation_guard_runs_before_clean_text() -> None:
    """A truncated fence has no closing ``` so it cannot be stripped.

    Checking `clean` first handed the raw fragment back as the answer.
    """
    src = _source()
    i_guard = src.index("if _trunc(text_out) or _blob(text_out):")
    i_clean = src.index("elif clean:", i_guard)
    assert i_guard < i_clean, "the truncation/blob guard must be evaluated first"
