from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from remedy.interfaces.api import create_app
from remedy.interfaces.api_support import load_config


def main() -> None:
    cfg = load_config()
    print("cfg scope", cfg.get("access_scope"), "project", cfg.get("project_path"))
    app = create_app()
    client = TestClient(app)
    token = None
    r = client.get("/api/auth/local-bootstrap")
    print("bootstrap", r.status_code)
    if r.status_code == 200:
        token = (r.json() or {}).get("token")
    headers = {"Authorization": f"Bearer {token}"} if token else {}

    img = r"C:\Users\Administrator\OneDrive\Pictures\Screenshots\dog.png"
    res = client.get("/api/media", params={"path": img}, headers=headers)
    print(
        "onedrive",
        res.status_code,
        res.text[:300],
        res.headers.get("content-type"),
    )

    res2 = client.get(
        "/api/media",
        params={"path": "assets/remedy_icon.png"},
        headers=headers,
    )
    print(
        "project icon",
        res2.status_code,
        res2.headers.get("content-type"),
        len(res2.content),
    )

    # Show what roots media endpoint would consider via runtime if present
    from remedy.core.agent import BasicRuntime

    # Try to inspect allowed_roots helper source location
    import remedy.core.agent as agent_mod

    src = Path(agent_mod.__file__).read_text(encoding="utf-8")
    for i, line in enumerate(src.splitlines(), 1):
        if "allowed_roots" in line or "access_scope" in line and i < 50:
            pass


if __name__ == "__main__":
    main()
