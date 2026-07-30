"""Mission store and advance."""

from pathlib import Path

import pytest

from remedy.core.mission import (
    Mission,
    MissionStore,
    advance_step,
    create_mission,
    mission_summary,
)


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
