"""Project scan path jail — Go httpapi owns POST /api/projects/scan.

Python TestClient no longer registers the twin; jail semantics live in
native/go/httpapi/projects_scan.go (+ TestProjectsScan).
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app


def test_projects_scan_absent_from_testclient():
    client = TestClient(create_app(api_key=""))
    r = client.post("/api/projects/scan", params={"path": "."})
    assert r.status_code in (404, 405)
