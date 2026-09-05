"""DELETE /api/skills/{name} is Go-owned (httpapi handleDeleteSkill).

Keep a boundary check that the TestClient FastAPI surface no longer serves it.
"""

from __future__ import annotations

from remedy.interfaces.api import create_app


def test_skill_delete_absent_from_testclient():
    from fastapi.testclient import TestClient

    client = TestClient(create_app(api_key=""))
    r = client.delete("/api/skills/temp-library-skill")
    assert r.status_code in (404, 405)
