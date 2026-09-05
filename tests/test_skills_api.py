"""HTTP /api/skills surface is Go-owned (httpapi skills.go / skills_extras).

Keep a boundary check that the TestClient FastAPI surface no longer serves it.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app


def test_skills_routes_absent_from_testclient():
    client = TestClient(create_app(api_key=""))
    assert client.get("/api/skills").status_code in (404, 405)
    assert client.get("/api/skills/api-skill").status_code in (404, 405)
    assert client.post("/api/skills/api-skill/status", json={"status": "disabled"}).status_code in (
        404,
        405,
    )
    assert client.post(
        "/api/skills/api-skill/quarantine", json={"quarantine": True}
    ).status_code in (404, 405)
    assert client.put(
        "/api/skills/api-skill/body", json={"instructions": "# x\n"}
    ).status_code in (404, 405)


# GET /api/skills/learning/summary is owned by Go httpapi (skills_extras).
