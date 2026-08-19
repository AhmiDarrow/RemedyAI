"""Labelling what the owner just asked for.

The label is not cosmetic: tools are stripped on the cheap tier for anything
labelled `chat`. So a request to browse or run something that comes back as
`chat` does not merely get filed wrongly — Remedy loses the tools she needed to
answer it, and the owner sees her decline something she can do.

That makes the false negatives the interesting direction. Everything below that
should be `tool` is a capability she keeps or loses.
"""

from __future__ import annotations

import pytest

from remedy.nanoswarm.router_nanobot import RouterNanobot


@pytest.fixture()
def router():
    return RouterNanobot()


def label(router, text: str) -> str:
    return router.classify_intent(text)["label"]


# --- ordinary conversation ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    ["", "   ", "hello", "how are you", "thanks!", "what do you think of this idea"],
)
def test_conversation_stays_conversation(router, text):
    assert label(router, text) == "chat"


# --- work that needs tools ----------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "run the tests",
        "execute the build",
        "npm install please",
        "pip install requests",
        "git status",
        "implement the parser",
        "refactor this module",
        "debug the failing test",
        "fix the bug in the loader",
        "create a file called notes.md",
        "edit the file src/app.py",
        "write a test for the parser",
        "commit that",
        "push it",
        "open the pr",
    ],
)
def test_asking_for_work_is_labelled_as_needing_tools(router, text):
    assert label(router, text) == "tool"


@pytest.mark.parametrize(
    "text",
    [
        "navigate to example.com",
        "browse the docs",
        "go to gmail",
        "goto github",
        "open https://example.com",
        "open gmail",
        "open github",
        "take a screenshot",
        "click the login button",
        "type into the search box",
    ],
)
def test_computer_use_is_never_left_as_chat(router, text):
    """Tools are stripped on chat; a browse request that lands there cannot run."""
    assert label(router, text) == "tool"


@pytest.mark.parametrize(
    "text",
    ["list_dir src", "repo_search parser", "file_read app.py", "spread_run over the repo"],
)
def test_a_tool_named_outright_is_a_tool_request(router, text):
    assert label(router, text) == "tool"


def test_searching_the_codebase_is_a_tool_request(router):
    assert label(router, "search the codebase for the parser") == "tool"


# --- memory -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "/memory dentist",
        "remember that bin night is Tuesday",
        "remember my sister's birthday",
        "what do you know about me",
        "what do you remember about the project",
        "recall what I said yesterday",
        "forget that",
        "did i tell you about the move",
    ],
)
def test_memory_requests_are_labelled_memory(router, text):
    assert label(router, text) == "memory"


# --- planning -----------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "/plan the migration",
        "plan the week",
        "make a plan for the rewrite",
        "walk me through it step by step",
        "give me a roadmap",
        "break this down",
        "break it down for me",
        "write an implementation plan",
    ],
)
def test_planning_requests_are_labelled_plan(router, text):
    assert label(router, text) == "plan"


# --- skills -------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "/skills",
        "/skill list",
        "use the skill for deploys",
        "which skill handles this",
        "run the backup skill",
        "activate that skill",
    ],
)
def test_skill_requests_are_labelled_skill(router, text):
    assert label(router, text) == "skill"


# --- working alone ------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "work alone on this",
        "carry on on your own",
        "handle this on your own",
        "i need to go, keep going",
        "i'm going to step away",
        "don't wait for me",
        "do not wait for me",
        "run unattended",
        "be fully autonomous",
        "finish without me",
        "take it from here",
    ],
)
def test_being_left_to_it_is_recognised(router, text):
    """This is the owner handing over; mistaking it for chat wastes the window."""
    assert label(router, text) == "autonomous"


def test_working_alone_wins_over_the_work_words_in_the_same_sentence(router):
    """"I need to go — run the tests" is a handover, not just a command."""
    assert label(router, "i need to go, run the tests and fix anything broken") == (
        "autonomous"
    )


# --- ambiguity ----------------------------------------------------------------


def test_a_long_unclassified_message_is_flagged_ambiguous(router):
    out = router.classify_intent("x" * 80)
    assert out["label"] == "chat"
    assert out["ambiguous"] is True


def test_a_short_chat_message_is_not_flagged_ambiguous(router):
    assert router.classify_intent("hello")["ambiguous"] is False


def test_a_classified_message_is_never_ambiguous(router):
    assert router.classify_intent("run the tests")["ambiguous"] is False


# --- the result shape ---------------------------------------------------------


def test_the_result_says_who_produced_it(router):
    out = router.classify_intent("run the tests")
    assert out["bot"] == "router"
    assert out["method"] == "heuristic"


def test_the_last_label_is_recorded_for_status(router):
    router.classify_intent("run the tests")
    assert router.last_label == "tool"
    assert router.last_method == "heuristic"


def test_status_answers(router):
    router.classify_intent("hello")
    assert isinstance(router.status(), dict)


# --- the cache ----------------------------------------------------------------


def test_the_same_question_twice_is_a_cache_hit(router):
    router.classify_intent("run the tests")
    before = router.cache_hits
    router.classify_intent("run the tests")
    assert router.cache_hits == before + 1


def test_case_and_padding_do_not_defeat_the_cache(router):
    router.classify_intent("Run The Tests")
    before = router.cache_hits
    router.classify_intent("  run the tests  ")
    assert router.cache_hits == before + 1


def test_a_caller_cannot_corrupt_the_cache_by_editing_a_result(router):
    """A shared dict handed out by reference would poison every later hit."""
    first = router.classify_intent("run the tests")
    first["label"] = "tampered"
    assert router.classify_intent("run the tests")["label"] == "tool"


def test_an_empty_message_is_not_cached_as_a_key(router):
    before = router.cache_hits
    router.classify_intent("")
    router.classify_intent("")
    assert router.cache_hits == before


def test_the_cache_does_not_grow_without_bound(router):
    from remedy.nanoswarm.router_nanobot import _INTENT_CACHE_MAX

    for i in range(_INTENT_CACHE_MAX + 50):
        router.classify_intent(f"unique message number {i}")
    assert len(router._intent_cache) <= _INTENT_CACHE_MAX


def test_a_very_long_message_is_still_classified(router):
    assert router.classify_intent("run the tests " + "x" * 2000)["label"] == "tool"
