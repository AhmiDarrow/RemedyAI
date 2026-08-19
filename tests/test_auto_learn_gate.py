"""Auto-learn — deciding a turn was worth codifying into a skill.

The gate exists because of a specific failure: without it, three file_reads in
a row became a "skill", and the catalog filled with noise nobody could use.
So the thresholds are the subject here — how much real work, how much success,
how much variety — along with the titles, which used to embed the owner's
drive paths and produce skill ids like
``file_read-i-like-our-assets-for-remedy-located-at-c-users-…``.
"""

from __future__ import annotations

import pytest

from remedy.core.agent_learn import (
    _skill_title_from_steps,
    auto_learn_from_turn,
    should_auto_learn_from_steps,
)


def step(tool: str, *, success: bool = True) -> dict:
    return {"tool": tool, "success": success}


def work(*tools: str, success: bool = True) -> list[dict]:
    return [step(t, success=success) for t in tools]


# --- what does not deserve a skill ------------------------------------------


def test_nothing_at_all():
    assert should_auto_learn_from_steps(None) is False
    assert should_auto_learn_from_steps([]) is False


def test_a_two_step_turn_is_too_short():
    assert should_auto_learn_from_steps(work("file_read", "file_write")) is False


def test_reading_the_same_way_three_times_is_not_a_skill():
    """The original bug: file_read spam flooding the catalog."""
    assert should_auto_learn_from_steps(work("file_read", "file_read", "file_read")) is False


def test_a_pure_explore_loop_is_noise():
    assert (
        should_auto_learn_from_steps(work("list_dir", "list_dir", "list_dir", "list_dir"))
        is False
    )


def test_meta_tools_alone_do_not_count_as_work():
    """Searching for a skill is not doing one."""
    assert (
        should_auto_learn_from_steps(
            work("skill_search", "skill_activate", "skill_reload", "local_discover")
        )
        is False
    )


def test_a_turn_that_mostly_failed_is_not_a_lesson():
    steps = work("file_read", "file_write", "bash_exec")
    for s in steps:
        s["success"] = False
    assert should_auto_learn_from_steps(steps) is False


def test_three_successes_are_needed_however_long_the_turn():
    steps = work("a", "b", "c", "d", "e")
    for s in steps[2:]:
        s["success"] = False
    assert should_auto_learn_from_steps(steps) is False


def test_a_turn_that_half_failed_does_not_qualify():
    steps = work("a", "b", "c", "d", "e", "f", "g", "h")
    for s in steps[3:]:
        s["success"] = False
    assert should_auto_learn_from_steps(steps) is False


def test_one_distinct_tool_is_never_a_pattern():
    assert should_auto_learn_from_steps(work("bash_exec", "bash_exec", "bash_exec", "bash_exec")) is False


# --- what does ---------------------------------------------------------------


def test_four_successful_steps_across_three_tools():
    assert (
        should_auto_learn_from_steps(
            work("repo_search", "file_read", "file_write", "bash_exec")
        )
        is True
    )


def test_three_distinct_tools_is_enough_even_on_a_short_path():
    assert (
        should_auto_learn_from_steps(work("repo_search", "file_write", "bash_exec"))
        is True
    )


def test_real_work_alongside_meta_tools_still_counts():
    steps = work("skill_search", "repo_search", "file_write", "bash_exec", "file_read")
    assert should_auto_learn_from_steps(steps) is True


def test_a_mostly_successful_long_turn_qualifies():
    steps = work("repo_search", "file_read", "file_write", "bash_exec", "file_edit")
    steps[-1]["success"] = False
    assert should_auto_learn_from_steps(steps) is True


@pytest.mark.parametrize("key", ["tool", "name", "tool_name"])
def test_a_step_is_read_whichever_key_names_its_tool(key):
    """Three producers write these steps and they do not agree on the key."""
    steps = [
        {key: "repo_search", "success": True},
        {key: "file_write", "success": True},
        {key: "bash_exec", "success": True},
    ]
    assert should_auto_learn_from_steps(steps) is True


# --- titles ------------------------------------------------------------------


def test_a_title_is_built_from_the_tools_used():
    title = _skill_title_from_steps("anything", work("repo_search", "file_write"))
    assert title == "repo_search-file_write"


def test_a_title_stops_at_three_tools():
    title = _skill_title_from_steps("", work("a", "b", "c", "d", "e"))
    assert title == "a-b-c"


def test_meta_tools_are_left_out_of_the_title():
    title = _skill_title_from_steps("", work("skill_search", "repo_search", "file_write"))
    assert "skill_search" not in title


def test_a_repeated_tool_appears_once():
    title = _skill_title_from_steps("", work("file_read", "file_read", "file_write"))
    assert title == "file_read-file_write"


def test_without_tools_the_title_falls_back_to_the_message():
    assert _skill_title_from_steps("tidy the downloads folder", []) == (
        "tidy the downloads folder"
    )


def test_a_windows_path_is_stripped_out_of_the_fallback_title():
    """The bug this replaced: drive paths became part of the skill id."""
    title = _skill_title_from_steps(
        r"I like our assets for Remedy located at C:\Users\me\assets", []
    )
    assert "C:" not in title
    assert "Users" not in title


def test_a_posix_path_is_stripped_too():
    title = _skill_title_from_steps("check the logs in /var/log/remedy", [])
    assert "/var" not in title


def test_only_the_first_line_of_a_long_message_is_used():
    title = _skill_title_from_steps("first line\nsecond line\nthird", [])
    assert title == "first line"


def test_a_title_is_bounded():
    assert len(_skill_title_from_steps("x" * 500, [])) <= 60


def test_an_empty_message_and_no_steps_still_names_something():
    assert _skill_title_from_steps("", []) == "session-task"


def test_a_message_of_nothing_but_a_path_still_names_something():
    assert _skill_title_from_steps("/only/a/path", []) == "session-task"


# --- the learning call itself ------------------------------------------------


class Loop:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def learn_from_tool_steps(self, **kw):
        self.calls.append(kw)
        return None


def test_no_learning_loop_means_no_learning():
    assert auto_learn_from_turn(
        learning_loop=None, message="m", session_id="s", steps=work("a", "b", "c", "d")
    ) is None


def test_an_ineligible_turn_never_reaches_the_loop():
    loop = Loop()
    auto_learn_from_turn(
        learning_loop=loop, message="m", session_id="s", steps=work("file_read")
    )
    assert loop.calls == []


def test_an_eligible_turn_is_distilled(monkeypatch):
    loop = Loop()
    monkeypatch.setattr(
        "remedy.nanoswarm.get_swarm",
        lambda: type("S", (), {"dispatch": lambda self, e: {"signals": {}}})(),
    )
    auto_learn_from_turn(
        learning_loop=loop,
        message="tidy up",
        session_id="s1",
        steps=work("repo_search", "file_write", "bash_exec", "file_read"),
    )
    assert loop.calls
    assert loop.calls[0]["session_id"] == "s1"
    assert loop.calls[0]["overall_success"] is True


def test_the_pregate_can_veto_a_turn(monkeypatch):
    """The nanobot saw this pattern before and it was not worth keeping."""
    loop = Loop()
    monkeypatch.setattr(
        "remedy.nanoswarm.get_swarm",
        lambda: type(
            "S",
            (),
            {
                "dispatch": lambda self, e: {
                    "signals": {"pattern_pregate": {"skip_learn": True, "reasoning": "noisy"}}
                }
            },
        )(),
    )
    auto_learn_from_turn(
        learning_loop=loop,
        message="m",
        session_id="s",
        steps=work("repo_search", "file_write", "bash_exec", "file_read"),
    )
    assert loop.calls == []


@pytest.mark.parametrize("action", ["reject", "skip"])
def test_a_rejecting_pregate_action_also_vetoes(monkeypatch, action):
    loop = Loop()
    monkeypatch.setattr(
        "remedy.nanoswarm.get_swarm",
        lambda: type(
            "S",
            (),
            {"dispatch": lambda self, e: {"signals": {"pattern_pregate": {"action": action}}}},
        )(),
    )
    auto_learn_from_turn(
        learning_loop=loop,
        message="m",
        session_id="s",
        steps=work("repo_search", "file_write", "bash_exec", "file_read"),
    )
    assert loop.calls == []


def test_a_broken_pregate_does_not_block_learning(monkeypatch):
    """Fail open here: the gate is an optimisation, not a safety boundary."""
    loop = Loop()

    def boom():
        raise RuntimeError("swarm unavailable")

    monkeypatch.setattr("remedy.nanoswarm.get_swarm", boom)
    auto_learn_from_turn(
        learning_loop=loop,
        message="m",
        session_id="s",
        steps=work("repo_search", "file_write", "bash_exec", "file_read"),
    )
    assert loop.calls
