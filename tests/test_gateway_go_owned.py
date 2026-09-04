"""Python messenger inbound is gone — Go owns poll/WS/webhooks."""

from __future__ import annotations

import importlib
import importlib.util
import re
from pathlib import Path

from remedy.gateway.messengers import list_messenger_definitions
from remedy.gateway.poll_lock import python_may_poll_messengers


def test_inbound_only_modules_are_gone() -> None:
    import remedy.gateway.channels as channels_pkg

    root = Path(channels_pkg.__file__).resolve().parent
    assert not (root / "jwt_rs256.py").exists()
    assert not (root / "emit_util.py").exists()
    assert importlib.util.find_spec("remedy.interfaces.routes.webhooks") is None


def test_production_cannot_enable_python_inbound(monkeypatch) -> None:
    monkeypatch.delenv("REMEDY_PYTHON_MESSENGER_POLL", raising=False)
    monkeypatch.delenv("REMEDY_TESTING", raising=False)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)
    assert python_may_poll_messengers() is False

    monkeypatch.setenv("REMEDY_PYTHON_MESSENGER_POLL", "1")
    assert python_may_poll_messengers() is False


def test_channel_modules_have_no_inbound_loops() -> None:
    """Source scan: outbound stubs must not contain poll/WS/webhook inbound."""
    root = Path("src/remedy/gateway/channels")
    banned = re.compile(
        r"handle_webhook_payload|verify_webhook_challenge|"
        r"async def _sync_loop|async def _gateway_loop|async def _socket_loop|"
        r"async def _receive_loop|async def _ws_loop|async def _poll_loop|"
        r"MessengerPollLock|python_may_poll_messengers|/getUpdates"
    )
    for name in (
        "telegram.py",
        "discord.py",
        "slack.py",
        "matrix.py",
        "mattermost.py",
        "signal_cli.py",
        "whatsapp.py",
        "teams.py",
        "google_chat.py",
    ):
        src = (root / name).read_text(encoding="utf-8")
        assert banned.search(src) is None, f"{name} still has inbound code"


def test_outbound_adapters_still_importable() -> None:
    for mod in (
        "remedy.gateway.channels.telegram",
        "remedy.gateway.channels.discord",
        "remedy.gateway.channels.slack",
        "remedy.gateway.channels.matrix",
        "remedy.gateway.channels.mattermost",
        "remedy.gateway.channels.whatsapp",
        "remedy.gateway.channels.teams",
        "remedy.gateway.channels.google_chat",
        "remedy.gateway.channels.signal_cli",
    ):
        assert importlib.import_module(mod) is not None


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
    import remedy.gateway as gw

    doc = gw.__doc__ or ""
    assert "Go" in doc or "remedy-runtime" in doc
    assert "inbound" in doc.lower()
