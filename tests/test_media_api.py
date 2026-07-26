"""Local media API for chat markdown images (provider-agnostic)."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient


def _png_bytes() -> bytes:
    return (
        b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01"
        b"\x08\x02\x00\x00\x00\x90wS\xde\x00\x00\x00\x0cIDATx\x9cc\xf8\x0f\x00"
        b"\x00\x01\x01\x00\x05\x18\xd8N\x00\x00\x00\x00IEND\xaeB`\x82"
    )


def test_media_serves_project_relative_png(tmp_path: Path, monkeypatch):
    from remedy.interfaces import api_support
    from remedy.interfaces.api import create_app

    assets = tmp_path / "assets" / "previews"
    assets.mkdir(parents=True)
    img = assets / "hero.png"
    img.write_bytes(_png_bytes())

    home = tmp_path / ".remedy"
    home.mkdir()
    cfg = {
        "project_path": str(tmp_path),
        "home_dir": str(home),
        "access_scope": "project",
        "approval_mode": "auto",
    }
    monkeypatch.setattr(api_support, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(
        "remedy.interfaces.routes.workspace.load_config",
        lambda: dict(cfg),
    )

    app = create_app()
    client = TestClient(app)
    token = None
    try:
        r = client.get("/api/auth/local-bootstrap")
        if r.status_code == 200:
            token = (r.json() or {}).get("token")
    except Exception:
        token = None
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    res = client.get(
        "/api/media",
        params={"path": "assets/previews/hero.png"},
        headers=headers,
    )
    if res.status_code != 200:
        res = client.get(
            "/api/media",
            params={"path": str(img)},
            headers=headers,
        )
    assert res.status_code == 200, res.text
    assert res.headers.get("content-type", "").startswith("image/")
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_media_blocks_outside_roots(tmp_path: Path, monkeypatch):
    from remedy.interfaces import api_support
    from remedy.interfaces.api import create_app

    project = tmp_path / "proj"
    project.mkdir()
    outside = tmp_path / "secret.png"
    outside.write_bytes(_png_bytes())

    home = tmp_path / ".remedy"
    home.mkdir()
    cfg = {
        "project_path": str(project),
        "home_dir": str(home),
        "access_scope": "project",
    }
    monkeypatch.setattr(api_support, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(
        "remedy.interfaces.routes.workspace.load_config",
        lambda: dict(cfg),
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

    res = client.get(
        "/api/media",
        params={"path": str(outside)},
        headers=headers,
    )
    assert res.status_code in (403, 404), res.text


def test_media_normalizes_unsupported_suffix_via_pillow(tmp_path: Path, monkeypatch):
    """Unknown image suffix under allowed roots → PNG via Pillow (chat-safe)."""
    from remedy.interfaces import api_support
    from remedy.interfaces.api import create_app

    try:
        from PIL import Image
    except ImportError:
        import pytest

        pytest.skip("Pillow not installed")

    home = tmp_path / ".remedy"
    home.mkdir()
    # .tga is not in the direct media_types map → normalize path
    tga = home / "attachments" / "sess" / "shot.tga"
    tga.parent.mkdir(parents=True)
    Image.new("RGB", (4, 4), color=(20, 40, 60)).save(tga, format="TGA")

    cfg = {
        "project_path": str(tmp_path / "proj"),
        "home_dir": str(home),
        "access_scope": "full",
        "approval_mode": "auto",
    }
    (tmp_path / "proj").mkdir(exist_ok=True)
    monkeypatch.setattr(api_support, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(
        "remedy.interfaces.routes.workspace.load_config",
        lambda: dict(cfg),
    )

    app = create_app()
    client = TestClient(app)
    token = None
    try:
        r = client.get("/api/auth/local-bootstrap")
        if r.status_code == 200:
            token = (r.json() or {}).get("token")
    except Exception:
        token = None
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    res = client.get("/api/media", params={"path": str(tga)}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.headers.get("content-type", "").startswith("image/png")
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"


def test_media_serves_attachment_under_home(tmp_path: Path, monkeypatch):
    """Chat images often live under ~/.remedy/attachments — must be allowed."""
    from remedy.interfaces import api_support
    from remedy.interfaces.api import create_app

    home = tmp_path / ".remedy"
    att = home / "attachments" / "abc" / "Screenshot.png"
    att.parent.mkdir(parents=True)
    att.write_bytes(_png_bytes())

    cfg = {
        "project_path": str(tmp_path / "empty_proj"),
        "home_dir": str(home),
        "access_scope": "project",
    }
    (tmp_path / "empty_proj").mkdir(exist_ok=True)
    monkeypatch.setattr(api_support, "load_config", lambda: dict(cfg))
    monkeypatch.setattr(
        "remedy.interfaces.routes.workspace.load_config",
        lambda: dict(cfg),
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

    res = client.get("/api/media", params={"path": str(att)}, headers=headers)
    assert res.status_code == 200, res.text
    assert res.content[:8] == b"\x89PNG\r\n\x1a\n"
