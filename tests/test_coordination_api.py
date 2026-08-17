"""/api/coordination/presence — the desktop's view of Remedy's working body."""

from __future__ import annotations

from fastapi.testclient import TestClient

from remedy.core import coordination as C
from remedy.interfaces.api import create_app


def test_presence_endpoint_lists_live_muscles(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "rh"))
    C.register(
        "grok-sess-1",
        muscle="xai/grok-4.5",
        project_path=str(tmp_path / "Old-Remedy"),
        goal="ship 0.27",
        phase="implement",
    )
    C.claim_path("grok-sess-1", tmp_path / "Old-Remedy" / "build_engine.py")

    app = create_app()
    client = TestClient(app)
    r = client.get("/api/coordination/presence", params={"session_id": "fable-sess-2"})
    assert r.status_code == 200
    data = r.json()
    assert data["count"] == 1
    b = data["beacons"][0]
    assert b["session_id"] == "grok-sess-1"
    assert b["you"] is False
    assert b["muscle"] == "xai/grok-4.5"
    assert b["project"] == "Old-Remedy"
    assert b["goal"] == "ship 0.27"
    assert b["phase"] == "implement"
    assert "build_engine.py" in b["held_files"]


def test_presence_marks_own_session_as_you(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "rh"))
    C.register("me-1", muscle="fable", phase="scout")
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/coordination/presence", params={"session_id": "me-1"})
    assert r.status_code == 200
    beacons = r.json()["beacons"]
    assert [b["you"] for b in beacons if b["session_id"] == "me-1"] == [True]


def test_presence_empty_when_quiet(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "rh-empty"))
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/coordination/presence")
    assert r.status_code == 200
    assert r.json() == {"beacons": [], "count": 0, "ts": r.json()["ts"]}
