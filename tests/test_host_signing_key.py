"""Durable Zig HMAC signing key in the host secret store."""

from __future__ import annotations

import base64
from pathlib import Path

import pytest

from remedy.core.computer import host_binding as H


def test_load_or_create_host_signing_key_persists(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("REMEDY_SPAWN_SIGNING_KEY", raising=False)
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    key1 = H.load_or_create_host_signing_key(tmp_path)
    assert len(key1) == 32
    key2 = H.load_or_create_host_signing_key(tmp_path)
    assert key1 == key2
    primary = tmp_path / "auth" / "host_signing_key"
    assert primary.is_file()
    # Must never leave raw key bytes as a bare 32-byte file.
    raw = primary.read_bytes()
    assert raw != key1
    assert b"host_signing_key" in raw or b"dpapi" in raw or base64.b64encode(key1)[:16] in raw


def test_decode_rejects_wrong_kind() -> None:
    blob = b'{"v":2,"kind":"other","dpapi":"QQ=="}'
    assert H._decode_host_signing_key(blob) is None


def test_ensure_spawn_signing_key_uses_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    if not H.available():
        pytest.skip("remedy_core not loaded")
    monkeypatch.delenv("REMEDY_SPAWN_SIGNING_KEY", raising=False)
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    H.security_clear_signing_key()
    H._signing_key_ready = False
    H.ensure_spawn_signing_key()
    assert H._signing_key_ready is True
    stored = H.load_or_create_host_signing_key(tmp_path)
    H.security_clear_signing_key()
    H._signing_key_ready = False
    H.ensure_spawn_signing_key()
    assert H.load_or_create_host_signing_key(tmp_path) == stored
