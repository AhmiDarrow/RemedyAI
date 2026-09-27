"""A completion claim must lose to the evidence.

The old gate read the model's prose: "Done, all tests pass" ended the turn even
when the last command had exited non-zero, so a build could report success over
a red test. The gate now decides on the last tool batch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.core.react_policy import failure_is_knowledge
from remedy.runtime.prompt_assemble import should_continue


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "anthropic"\nllm_model = "claude-opus-5"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


@pytest.mark.parametrize(
    "tail",
    [
        "ModuleNotFoundError: No module named 'foo'",
        "ImportError: cannot import name 'Bar'",
        "bash: flarb: command not found",
        "'git' is not recognized as an internal or external command",
        "Could not find a version that satisfies the requirement foo",
        "No matching distribution found for foo",
        "error: unknown command \"frob\"",
        "unrecognized option '--frob'",
        "HTTP 404",
        "404 Not Found",
    ],
)
def test_outside_failures_are_knowledge(tail: str) -> None:
    assert failure_is_knowledge(tail)


@pytest.mark.parametrize(
    "tail",
    [
        "FAILED test_app.py::test_verbose_flag",
        "AssertionError: expected 1",
        "SyntaxError: invalid syntax",
        "No such file or directory: src/app.py",
        "error: exit status 1",
        "command completed",
        "unknown",
    ],
)
def test_project_failures_are_not_knowledge(tail: str) -> None:
    assert not failure_is_knowledge(tail)


def test_completion_claim_is_refused_after_a_failing_verify(home: Path) -> None:
    out = should_continue(
        {
            "goal": "build the app",
            "text": "Done, all tests pass.",
            "session_id": "sess-red",
            "tool_count": 3,
            "verify_seen": False,
            "last_results": [
                {
                    "name": "bash",
                    "ok": True,
                    "exit_code": 1,
                    "tail": "exit_code=1 FAILED test_app.py::test_verbose_flag",
                }
            ],
        }
    )
    assert out.get("ok") is True, out.get("error")
    assert out.get("continue") is True, "a red verify must not be reported as done"
    nudge = str(out.get("nudge") or "")
    assert nudge.strip(), "the model needs to be told what failed"


def test_completion_is_accepted_after_a_green_verify(home: Path) -> None:
    out = should_continue(
        {
            "goal": "build the app",
            "text": "Done, all tests pass.",
            "session_id": "sess-green",
            "tool_count": 3,
            "verify_seen": True,
            "last_results": [
                {"name": "bash", "ok": True, "exit_code": 0, "tail": "exit_code=0 5 passed"}
            ],
        }
    )
    assert out.get("ok") is True, out.get("error")
    assert out.get("continue") is False, "a green verify should end the turn"
    assert "web.search" not in str(out.get("nudge") or "")


def test_a_red_test_is_a_code_failure_not_a_search(home: Path) -> None:
    out = should_continue(
        {
            "goal": "build the app",
            "text": "Done, all tests pass.",
            "session_id": "sess-code",
            "tool_count": 2,
            "last_results": [
                {
                    "name": "bash",
                    "ok": False,
                    "exit_code": 1,
                    "tail": "exit_code=1 FAILED test_app.py::test_verbose_flag AssertionError",
                }
            ],
        }
    )
    assert out.get("continue") is True
    assert out.get("reason") == "verify_failed"
    nudge = str(out.get("nudge") or "")
    assert "web.search" not in nudge
    assert "fix it" in nudge.lower()


def test_a_missing_module_is_looked_up_once(home: Path) -> None:
    out = should_continue(
        {
            "goal": "build the app",
            "text": "Done. The feature is finished.",
            "session_id": "sess-know",
            "tool_count": 1,
            "last_results": [
                {
                    "name": "bash",
                    "ok": False,
                    "exit_code": 1,
                    "tail": "ModuleNotFoundError: No module named 'remedy.missing'",
                }
            ],
        }
    )
    assert out.get("continue") is True
    assert out.get("reason") == "knowledge_gap"
    assert "web.search" in str(out.get("nudge") or "")


def test_the_same_red_check_can_be_tried_again(home: Path) -> None:
    payload = {
        "goal": "build the app",
        "text": "Done, all tests pass.",
        "session_id": "sess-same",
        "tool_count": 2,
        "last_results": [
            {
                "name": "bash",
                "ok": False,
                "exit_code": 1,
                "tail": "exit_code=1 FAILED test_app.py::test_verbose_flag",
            }
        ],
    }
    for _ in range(6):
        out = should_continue(payload)
        assert out.get("continue") is True
        assert out.get("reason") == "verify_failed"
        assert "Do not run" not in str(out.get("nudge") or "")


def test_giving_up_on_an_outside_fact_searches_once(home: Path) -> None:
    out = should_continue(
        {
            "goal": "implement the config flag in the runtime",
            "text": "I don't know how this API works, so I stopped.",
            "session_id": "sess-giveup",
            "tool_count": 1,
            "last_results": [
                {"name": "read", "ok": True, "tail": "class Config: pass"}
            ],
        }
    )
    assert out.get("continue") is True
    assert out.get("reason") == "knowledge_gap"
    assert "web.search" in str(out.get("nudge") or "")


def test_a_search_already_done_does_not_search_again(home: Path) -> None:
    out = should_continue(
        {
            "goal": "implement the config flag in the runtime",
            "text": "I don't know how this API works. Stopping here.",
            "session_id": "sess-searched",
            "tool_count": 2,
            "last_results": [
                {"name": "web.search", "ok": True, "tail": "no matching page"},
            ],
        }
    )
    assert out.get("reason") != "knowledge_gap"
