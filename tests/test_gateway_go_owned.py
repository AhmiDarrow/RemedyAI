"""Python gateway package is gone — Go owns messengers + catalog."""

from __future__ import annotations

import importlib.util
import sys

from remedy.interfaces.cli.parser import build_parser
from remedy.interfaces.messenger_catalog import list_messenger_definitions


def test_gateway_package_is_gone() -> None:
    assert importlib.util.find_spec("remedy.gateway") is None
    assert "remedy.gateway" not in sys.modules


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


def test_settings_catalog_still_has_field_schema() -> None:
    """TestClient settings schema stays in messenger_catalog; Go owns production."""
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
