"""Project scan path jail — never recon under auth or outside scope roots."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _client(tmp_path: Path, monkeypatch, *, project: Path | None = None):
    from remedy.interfaces import api_support
    from remedy.interfaces.api import create_app

    proj = project or (tmp_path / "proj")
    proj.mkdir(parents=True, exist_ok=True)
    (proj / "main.py").write_text("print('hi')\n", encoding="utf-8")
    home = tmp_path / ".remedy"
    home.mkdir(exist_ok=True)
    cfg = {
        "project_path": str(proj),
        "home_dir": str(home),
        "access_scope": "project",
    }
    monkeypatch.setattr(api_support, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(
        "remedy.interfaces.routes.misc.load_config",
        lambda: dict(cfg),
        raising=False,
    )
    # load_config is imported inside the handler from api_support
    monkeypatch.setattr(
        "remedy.interfaces.api_support.load_config",
        lambda: dict(cfg),
        raising=False,
    )
    app = create_app()
    client = TestClient(app)
    token = None
    try:
        r = client.get("/api/auth/local-bootstrap")
        if r.status_code == 200:
            token = (r.json() or {}).get("token")
    except Exception:
        pass
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    return client, headers, proj, home


def test_scan_allows_project_root(tmp_path: Path, monkeypatch):
    client, headers, proj, _home = _client(tmp_path, monkeypatch)
    res = client.post(
        "/api/projects/scan",
        params={"path": str(proj)},
        headers=headers,
    )
    assert res.status_code == 200, res.text
    data = res.json()
    assert data.get("file_counts", {}).get("python", 0) >= 1


def test_scan_refuses_auth_tree(tmp_path: Path, monkeypatch):
    client, headers, _proj, home = _client(tmp_path, monkeypatch)
    auth = home / "auth"
    auth.mkdir(parents=True)
    (auth / "provider_keys.json").write_text('{"k":1}', encoding="utf-8")

    res = client.post(
        "/api/projects/scan",
        params={"path": str(auth)},
        headers=headers,
    )
    assert res.status_code in (400, 403), res.text
    assert "not allowed" in (res.text or "").lower() or "protected" in (
        res.text or ""
    ).lower()


def test_scan_refuses_outside_project(tmp_path: Path, monkeypatch):
    client, headers, _proj, _home = _client(tmp_path, monkeypatch)
    outside = tmp_path / "other_tree"
    outside.mkdir()
    (outside / "secret.py").write_text("x=1\n", encoding="utf-8")

    res = client.post(
        "/api/projects/scan",
        params={"path": str(outside)},
        headers=headers,
    )
    assert res.status_code in (400, 403), res.text
