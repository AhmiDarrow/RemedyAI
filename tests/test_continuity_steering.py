"""Continuity steering + builder verify advances."""

from __future__ import annotations

from pathlib import Path

from remedy.core.continuity_steering import continuity_steering_block
from remedy.core.metabolism.verify import verify_critical
from remedy.core.retention import RetentionPolicy
from remedy.memory.harness.brief import SessionBrief


def test_continuity_steering_includes_open_tasks() -> None:
    brief = SessionBrief(
        session_id="s1",
        intent="ship login",
        open_tasks=["wire OAuth", "add tests"],
        next_steps=["file_edit routes"],
        key_paths=["src/auth.py"],
        user_constraints=["no new deps"],
    )

    class R:
        _session_brief = brief

        def effective_project_path(self):
            return ""

    block = continuity_steering_block(
        R(), home=Path("/no/such/soul/home"), max_chars=1200
    )
    assert "Continuity" in block
    assert "ship login" in block
    assert "wire OAuth" in block
    assert "no new deps" in block
    assert "Do not claim done" in block or "verify" in block.lower()


def test_continuity_empty_without_open_work(tmp_path: Path) -> None:
    class R:
        _session_brief = SessionBrief(session_id="empty")
        config = type("C", (), {"home_dir": str(tmp_path / "empty_home")})()

        def effective_project_path(self):
            return ""

    # Isolated home so global ~/.remedy soul threads do not leak into the test.
    assert continuity_steering_block(R(), home=tmp_path / "empty_home") == ""


def test_verify_blocks_done_without_tools() -> None:
    r = verify_critical(assistant_text="All done, everything is shipped and ready.")
    assert r.ok is False
    assert r.kind == "done_without_tools"
    assert r.silent_remedy


def test_retention_defaults_not_forever() -> None:
    p = RetentionPolicy.from_config({})
    assert p.session_days == 180
    assert p.attachment_days == 90
    assert p.computer_shot_days == 14
    # Explicit 0 still disables
    p0 = RetentionPolicy.from_config({"retention_session_days": 0})
    assert p0.session_days == 0
