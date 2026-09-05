"""Python messenger network twins are gone — Go owns poll/WS/webhooks/outbound."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import remedy.gateway as gw
from remedy.gateway.messengers import list_messenger_definitions


def _gateway_root() -> Path:
    return Path(gw.__file__).resolve().parent


def test_inbound_and_adapter_modules_are_gone() -> None:
    root = _gateway_root()
    assert root.is_dir()
    assert not (root / "channels").exists()
    assert not (root / "router.py").exists()
    assert not (root / "session_bridge.py").exists()
    assert not (root / "serve_bootstrap.py").exists()
    assert not (root / "channel_registry.py").exists()
    assert not (root / "channel_hot_reload.py").exists()
    assert not (root / "poll_lock.py").exists()
    assert importlib.util.find_spec("remedy.interfaces.routes.webhooks") is None
    # Submodules of this package path must not resolve when twins are deleted.
    assert (root / "messengers.py").is_file()
    assert (root / "cli.py").is_file()


def test_messengers_catalog_still_has_field_schema() -> None:
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


def test_gateway_package_doc_names_go_owner() -> None:
    doc = gw.__doc__ or ""
    assert "Go" in doc or "remedy-runtime" in doc
    assert "outbound" in doc.lower() or "inbound" in doc.lower()
