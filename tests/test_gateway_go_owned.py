"""Python gateway package is gone — Go owns messengers + catalog JSON SSOT."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from remedy.interfaces.cli.parser import build_parser
from remedy.interfaces.messenger_settings import list_messenger_definitions


def test_gateway_package_is_gone() -> None:
    assert importlib.util.find_spec("remedy.gateway") is None
    assert "remedy.gateway" not in sys.modules
    assert importlib.util.find_spec("remedy.interfaces.messenger_catalog") is None


def test_cli_parser_still_exposes_gateway() -> None:
    ns = build_parser().parse_args(["gateway", "channels"])
    assert ns.command == "gateway"
    assert ns.gateway_cmd == "channels"


def test_gateway_start_fails_closed_to_go() -> None:
    from remedy.interfaces.cli.cmd_gateway import run_gateway_start

    try:
        run_gateway_start()
    except SystemExit as ei:
        msg = str(ei)
        assert "remedy-runtime" in msg or "Go" in msg
    else:
        raise AssertionError("expected SystemExit")


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
