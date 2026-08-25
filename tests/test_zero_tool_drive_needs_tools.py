"""The zero-tool drive must not demand what the turn cannot supply.

When a turn ends with no tool calls but work looks unfinished, the ReAct loop
re-arms tools and injects "you made **zero** tool_calls ... your next message
MUST be native function calls". But ``_rearm_agency_tools`` deliberately
declines on chat/trivia turns — so the drive could demand a native call while
no schema was armed. The model cannot comply, the same nudge re-fires, and
``_open_drive_keeps_going()`` lets it past ``max_zero_tool_drives``.

Observed live: 148 identical CONTINUE BUILD injects on a turn whose request was
"Reply with exactly the single word READY and nothing else. Do not use any
tools." Remedy had correctly routed it to a no-tools path; the drive then spent
the whole step budget insisting on tool calls that did not exist.

The drive is gated on tools actually being armed. These tests pin the gate
against the real loop source rather than a re-implementation of it.
"""

from __future__ import annotations

import inspect
import re

from remedy.core.react_loop import loop as loop_mod
from remedy.core.react_loop import loop_steps as steps_mod


def _source() -> str:
    """Orchestrator + extracted step loop (split so mypy can type both)."""
    return inspect.getsource(loop_mod) + "\n" + inspect.getsource(steps_mod)


def test_drive_bails_out_when_no_tools_are_armed() -> None:
    """_drive_zero_tool_work must return False rather than nudge blindly."""
    src = _source()
    start = src.index("def _drive_zero_tool_work(")
    end = src.index("# One OAuth/API re-auth attempt per turn", start)
    body = src[start:end]

    rearm = body.index("_rearm_agency_tools()")
    guard = body.index("if not tools:")
    assert guard > rearm, "the no-tools guard must run after the re-arm attempt"

    # The nudge and the counter must both sit behind the guard.
    assert body.index("zero_tool_drive_count += 1") > guard
    assert body.index("unfinished_work_nudge_message()") > guard


def test_guard_returns_false_not_continue() -> None:
    src = _source()
    start = src.index("def _drive_zero_tool_work(")
    body = src[start : src.index("# One OAuth/API re-auth attempt per turn", start)]
    tail = body[body.index("if not tools:") :]
    # Whatever logging happens, the branch must end the drive.
    assert re.search(r"return False", tail.split("zero_tool_drive_count")[0])


def test_step_loop_finalizes_when_rearm_declines() -> None:
    """The other drive site must let the turn answer in words."""
    src = _source()
    anchor = src.index("# Work request + zero tool evidence is never a successful final.")
    block = src[anchor : anchor + 1600]
    rearm = block.index("_rearm_agency_tools()")
    assert "if tools:" in block[rearm:], "must check whether re-arm actually armed"
    after = block[rearm:]
    # The declined branch has to stop forcing tool_choice and finalize.
    assert "force_answer = True" in after
    assert "is_final_step = True" in after


def test_max_zero_tool_drives_is_still_bounded() -> None:
    src = _source()
    assert "max_zero_tool_drives = 8" in src
