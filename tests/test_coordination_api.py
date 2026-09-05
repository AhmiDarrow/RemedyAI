"""Coordination presence is Go-owned; keep registry helpers under TestClient boundary."""

from __future__ import annotations

import os
import time

from remedy.core import coordination as C
from remedy.interfaces.api import create_app


def test_coordination_presence_http_absent_from_testclient() -> None:
    paths = {getattr(r, "path", "") for r in create_app(api_key="").routes}
    assert "/api/coordination/presence" not in paths


def _presence_payload(session_id: str | None = None) -> dict:
    """Mirror the Go/desktop shape from the Python registry helpers."""
    beacons: list[dict] = []
    now = time.time()
    for b in C.active_beacons():
        held = sorted(os.path.basename(p) for p in b.live_claims(now))
        beacons.append(
            {
                "session_id": b.session_id,
                "you": bool(session_id and b.session_id == session_id),
                "muscle": b.muscle,
                "project": os.path.basename((b.project_path or "").rstrip("/\\"))
                or b.project_path
                or "",
                "project_path": b.project_path,
                "goal": b.goal,
                "phase": b.phase,
                "held_files": held[:12],
                "held_count": len(held),
                "age_seconds": max(0, int(now - b.started_ts)),
                "heartbeat_seconds_ago": max(0, int(now - b.heartbeat_ts)),
            }
        )
    return {"beacons": beacons, "count": len(beacons), "ts": now}


def test_presence_lists_live_muscles(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "rh"))
    C.register(
        "grok-sess-1",
        muscle="xai/grok-4.5",
        project_path=str(tmp_path / "Old-Remedy"),
        goal="ship 0.27",
        phase="implement",
    )
    C.claim_path("grok-sess-1", tmp_path / "Old-Remedy" / "build_engine.py")

    data = _presence_payload(session_id="fable-sess-2")
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
    beacons = _presence_payload(session_id="me-1")["beacons"]
    assert [b["you"] for b in beacons if b["session_id"] == "me-1"] == [True]


def test_presence_empty_when_quiet(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path / "rh-empty"))
    data = _presence_payload()
    assert data["beacons"] == []
    assert data["count"] == 0


def test_presence_read_does_not_rewrite_registry(tmp_path, monkeypatch) -> None:
    """Chrome polls must not take the exclusive write txn / rewrite presence.json."""
    home = tmp_path / "rh"
    monkeypatch.setenv("REMEDY_HOME", str(home))
    C.register("s1", muscle="xai/grok", phase="scout")
    path = C._registry_path(home)
    before = path.read_bytes()
    mtime = path.stat().st_mtime_ns
    data = _presence_payload()
    assert data["count"] == 1
    assert data["beacons"][0]["session_id"] == "s1"
    assert path.read_bytes() == before
    assert path.stat().st_mtime_ns == mtime
