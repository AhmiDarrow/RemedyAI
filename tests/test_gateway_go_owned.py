"""Python gateway package is gone — Go owns messengers + catalog JSON SSOT."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from remedy.interfaces.messenger_settings import list_messenger_definitions


def test_gateway_package_is_gone() -> None:
    assert importlib.util.find_spec("remedy.gateway") is None
    assert "remedy.gateway" not in sys.modules
    assert importlib.util.find_spec("remedy.interfaces.messenger_catalog") is None


def test_settings_catalog_loads_go_json_ssot() -> None:
    """TestClient helpers read native/go/httpapi/messenger_catalog.json fail-closed."""
    defs = list_messenger_definitions()
    assert {m.id for m in defs} >= {
        "telegram",
        "discord",
        "slack",
        "mattermost",
        "whatsapp",
        "teams",
        "matrix",
        "google_chat",
        "signal",
    }
    for m in defs:
        assert m.fields, f"{m.id} missing fields"
        assert any(f.key for f in m.fields)


def test_python_loader_matches_go_json_bytes() -> None:
    root = Path(__file__).resolve().parents[1]
    go_fixture = root / "native" / "go" / "httpapi" / "messenger_catalog.json"
    assert go_fixture.is_file()
    raw = json.loads(go_fixture.read_text(encoding="utf-8"))
    assert isinstance(raw, list) and len(raw) >= 9
    assert [m.id for m in list_messenger_definitions()] == [
        str(row.get("id") or "").strip().lower() for row in raw if isinstance(row, dict)
    ]
