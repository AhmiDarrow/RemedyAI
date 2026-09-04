"""Python no longer owns Connect — CLI/settings hooks must not import it."""

from __future__ import annotations

import importlib
import importlib.util
import sys

import pytest

from remedy.interfaces.cli.parser import build_parser
from remedy.interfaces.settings_apply import (
    _connect_panes_public,
    _is_connect_wildcard_bind,
    _parse_connect_relay_endpoint,
)


def test_connect_package_is_gone() -> None:
    assert importlib.util.find_spec("remedy.connect") is None
    assert "remedy.connect" not in sys.modules


def test_cli_parser_still_exposes_connect_relay() -> None:
    ns = build_parser().parse_args(["connect-relay", "--host", "127.0.0.1", "--port", "7402"])
    assert ns.command == "connect-relay"
    assert ns.host == "127.0.0.1"
    assert ns.port == 7402


def test_cli_dispatches_to_go_binary(monkeypatch, tmp_path) -> None:
    M = importlib.import_module("remedy.interfaces.cli.main")
    called: dict[str, object] = {}

    def fake_call(cmd, *args, **kwargs):
        called["cmd"] = list(cmd)
        called["cwd"] = kwargs.get("cwd")
        return 0

    monkeypatch.setenv("REMEDY_CONNECT_RELAY", str(tmp_path / "connect-relay.exe"))
    monkeypatch.setattr(M.subprocess, "call", fake_call)
    monkeypatch.setattr(M, "_get_db_path", lambda home: tmp_path / "memory.db")
    with pytest.raises(SystemExit) as ei:
        M.main(["connect-relay", "--host", "10.0.0.8", "--port", "7402"])
    assert ei.value.code == 0
    assert called["cmd"] == [
        str(tmp_path / "connect-relay.exe"),
        "--host",
        "10.0.0.8",
        "--port",
        "7402",
    ]


def test_cli_connect_relay_hard_fails_without_go(monkeypatch, tmp_path) -> None:
    M = importlib.import_module("remedy.interfaces.cli.main")
    monkeypatch.delenv("REMEDY_CONNECT_RELAY", raising=False)
    monkeypatch.setattr(M.shutil, "which", lambda _name: None)
    monkeypatch.setattr(M, "_repo_root", lambda: None)
    monkeypatch.setattr(M, "_get_db_path", lambda home: tmp_path / "memory.db")
    with pytest.raises(SystemExit) as ei:
        M.main(["connect-relay", "--host", "127.0.0.1", "--port", "7402"])
    assert ei.value.code == 2


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
