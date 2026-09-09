"""A completion claim must lose to the evidence.

The old gate read the model's prose: "Done, all tests pass" ended the turn even
when the last command had exited non-zero, so a build could report success over
a red test. The gate now decides on the last tool batch.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from remedy.runtime.prompt_assemble import should_continue


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    (tmp_path / "config.toml").write_text(
        'name = "Remedy"\nllm_provider = "anthropic"\nllm_model = "claude-opus-5"\n',
        encoding="utf-8",
    )
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


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
                    "name": "shell.exec",
                    "ok": False,
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
                {"name": "shell.exec", "ok": True, "tail": "exit_code=0 5 passed"}
            ],
        }
    )
    assert out.get("ok") is True, out.get("error")
    assert out.get("continue") is False, "a green verify should end the turn"
