"""Python no longer owns Connect — CLI/settings hooks must not import it."""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest

from remedy.interfaces.settings_apply import (
    _connect_panes_public,
    _is_connect_wildcard_bind,
    _parse_connect_relay_endpoint,
)


def test_connect_package_is_gone() -> None:
    assert importlib.util.find_spec("remedy.connect") is None
    assert "remedy.connect" not in sys.modules


def test_settings_panes_and_wildcard_without_connect_pkg() -> None:
    panes = _connect_panes_public({"approvals": False, "computer_preview": True})
    assert panes["approvals"] is True
    assert panes["computer_preview"] is True
    assert _is_connect_wildcard_bind("0.0.0.0")
    assert _is_connect_wildcard_bind("::")
    assert not _is_connect_wildcard_bind("127.0.0.1")
    host, port = _parse_connect_relay_endpoint("192.0.2.9:7402")
    assert (host, port) == ("192.0.2.9", 7402)
    with pytest.raises(ValueError):
        _parse_connect_relay_endpoint("0.0.0.0:7402")
