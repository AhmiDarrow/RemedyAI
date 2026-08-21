"""Mission store and advance."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from remedy.core.build_mission import note_mission_verify
from remedy.core.errors import SecurityError
from remedy.core.mission import (
    Mission,
    MissionStore,
    advance_step,
    create_mission,
    mission_summary,
)
from remedy.core.security import sanitize_mission_session_id, validate_mission_id


def test_create_and_advance(tmp_path):
    m = create_mission(
        "Ship feature",
        steps=["Write code", "Run tests", "Docs"],
        verify_command="pytest -q",
        home=tmp_path,
    )
    assert m.status == "active"
    assert m.steps[0].status == "active"
    m = advance_step(m, status="done")
    store = MissionStore(tmp_path)
    store.save(m)
    loaded = store.latest()
    assert loaded is not None
    assert loaded.steps[0].status == "done"
    assert loaded.steps[1].status == "active"
    text = mission_summary(loaded)
    assert "Ship feature" in text
    assert "Verify:" in text


def test_get_by_short_prefix(tmp_path):
    m = create_mission("Goal", steps=["A"], home=tmp_path)
    store = MissionStore(tmp_path)
    short = m.id[:8]
    found = store.get(short)
    assert found is not None
    assert found.id == m.id


def test_mission_get_rejects_path_traversal(tmp_path: Path) -> None:
    """mission_id must not escape ~/.remedy/missions/."""
    store = MissionStore(tmp_path)
    # Create a secret *outside* missions that a naive join would hit
    secret = tmp_path / "auth" / "local_api_token.json"
    secret.parent.mkdir(parents=True)
    secret.write_text('{"token":"leak-me"}', encoding="utf-8")

    # Classic traversal + path separators
    assert store.get("../auth/local_api_token") is None
    assert store.get("..\\auth\\local_api_token") is None
    assert store.get("../../etc/passwd") is None
    assert store.get("/etc/passwd") is None
    assert store.get("foo/bar") is None
    assert store.get("") is None
    assert store.get("ab") is None  # too short for id rule


def test_mission_session_id_cannot_escape_pointer(tmp_path: Path) -> None:
    """session_id with path chars must not write latest-* outside missions/."""
    store = MissionStore(tmp_path)
    m = Mission(
        id="aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
        goal="x",
        session_id="../auth/evil",
    )
    store.save(m)
    # Pointer must not appear under auth/
    assert not (tmp_path / "auth" / "evil.txt").exists()
    assert not list((tmp_path / "missions").glob("latest-*../**"))
    # No traversal-shaped pointer file
    for p in (tmp_path / "missions").iterdir():
        assert ".." not in p.name
    # Sanitized session_id dropped → only global latest.txt
    assert (tmp_path / "missions" / "latest.txt").is_file()


def test_validate_mission_id_rejects_path_forms() -> None:
    """Central id gate — UUID/prefix ok; path/null/short refuse."""
    ok = validate_mission_id("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
    assert ok == "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    assert validate_mission_id("abcd") == "abcd"
    for bad in (
        "",
        "ab",
        "../auth/token",
        "..\\auth\\token",
        "foo/bar",
        "foo\\bar",
        "/etc/passwd",
        "a" * 80,
        "null\x00byte",
        "has space",
        "foo.json",  # dot not allowed — blocks extension tricks
        "sess..ion",
    ):
        with pytest.raises(SecurityError):
            validate_mission_id(bad)


def test_sanitize_mission_session_id_unit() -> None:
    assert sanitize_mission_session_id("ok_session.1") == "ok_session.1"
    assert sanitize_mission_session_id("../auth") is None
    assert sanitize_mission_session_id("a/b") is None
    assert sanitize_mission_session_id("a\\b") is None
    assert sanitize_mission_session_id("has..dots") is None
    assert sanitize_mission_session_id("") is None
    assert sanitize_mission_session_id(None) is None
    assert sanitize_mission_session_id("x" * 200) is None


def test_mission_save_rejects_forged_traversal_id(tmp_path: Path) -> None:
    store = MissionStore(tmp_path)
    with pytest.raises(SecurityError):
        store.save(Mission(id="../auth/local_api_token", goal="leak"))
    # Nothing written outside missions/
    assert not (tmp_path / "auth").exists()
    # No traversal-named file under missions either
    names = [p.name for p in (tmp_path / "missions").iterdir()] if (tmp_path / "missions").exists() else []
    assert not any(".." in n or "auth" in n for n in names)


def test_mission_poisoned_latest_pointer_ignored(tmp_path: Path) -> None:
    """latest.txt content with path traversal must not load outside missions/."""
    store = MissionStore(tmp_path)
    create_mission("real", steps=["A"], home=tmp_path)
    missions = tmp_path / "missions"
    # Overwrite pointer with classic traversal / absolute
    (missions / "latest.txt").write_text("../auth/local_api_token", encoding="utf-8")
    assert store.latest() is None
    (missions / "latest.txt").write_text("/etc/passwd", encoding="utf-8")
    assert store.latest() is None
    (missions / "latest.txt").write_text("..\\auth\\token", encoding="utf-8")
    assert store.latest() is None
    # Session pointer similarly ignored
    (missions / "latest-ok_session.txt").write_text("../auth/x", encoding="utf-8")
    assert store.latest(session_id="ok_session") is None


def test_green_verify_does_not_complete_implement_steps(tmp_path: Path) -> None:
    """First npm test pass is a checkpoint, not 'implement the goal' done."""
    m = create_mission(
        "Ship feature",
        steps=[
            "Scout codebase (batch reads)",
            "Implement the owner's goal (file_write / file_edit)",
            "Verify with project tests only after that work is on disk",
        ],
        verify_command="npm test",
        home=tmp_path,
    )
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=tmp_path))
    st = SimpleNamespace(mission_id=m.id)
    note_mission_verify(rt, st, ok=True, output="16/16 passed")
    loaded = MissionStore(tmp_path).get(m.id)
    assert loaded is not None
    assert loaded.verify_status == "passed"
    assert loaded.status == "active"
    by_title = {s.title: s.status for s in loaded.steps}
    assert by_title["Implement the owner's goal (file_write / file_edit)"] in (
        "pending",
        "active",
    )
    assert (
        by_title["Verify with project tests only after that work is on disk"] == "done"
    )


def test_green_verify_completes_mission_only_when_every_step_is_done(
    tmp_path: Path,
) -> None:
    m = create_mission(
        "Tiny",
        steps=["Verify with npm test"],
        verify_command="npm test",
        home=tmp_path,
    )
    rt = SimpleNamespace(config=SimpleNamespace(home_dir=tmp_path))
    note_mission_verify(rt, SimpleNamespace(mission_id=m.id), ok=True, output="ok")
    loaded = MissionStore(tmp_path).get(m.id)
    assert loaded is not None
    assert loaded.status == "completed"
    assert loaded.steps[0].status == "done"
