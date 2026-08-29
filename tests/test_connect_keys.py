"""Host static key store: persist, reload, DPAPI/plain envelope, no secret logs."""

from __future__ import annotations

import json
import logging
import stat
import sys
from pathlib import Path

import pytest

from remedy.connect.keys import host_key_path, load_or_create_host_keypair
from remedy.connect.noise import KeyPair


def test_host_key_persists_and_reloads_same_public(tmp_path: Path) -> None:
    first = load_or_create_host_keypair(tmp_path)
    second = load_or_create_host_keypair(tmp_path)
    assert first.public == second.public
    assert first.private == second.private
    assert len(first.public) == 32
    assert len(first.private) == 32
    path = tmp_path / "auth" / "connect" / "host.key"
    assert path.is_file()
    assert path == host_key_path(tmp_path)


def test_distinct_homes_get_distinct_keys(tmp_path: Path) -> None:
    a = load_or_create_host_keypair(tmp_path / "a")
    b = load_or_create_host_keypair(tmp_path / "b")
    assert a.public != b.public


def test_honours_remedy_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "env-home"
    monkeypatch.setenv("REMEDY_HOME", str(home))
    kp = load_or_create_host_keypair()
    path = home / "auth" / "connect" / "host.key"
    assert path.is_file()
    again = load_or_create_host_keypair()
    assert again.public == kp.public


def test_envelope_shape_and_private_not_in_plaintext(tmp_path: Path) -> None:
    kp = load_or_create_host_keypair(tmp_path)
    raw = host_key_path(tmp_path).read_bytes()
    text = raw.decode("utf-8")
    assert kp.private.hex() not in text
    assert kp.private not in raw
    outer = json.loads(text)
    assert outer["v"] == 2
    if sys.platform == "win32":
        assert outer.get("dpapi") is True
        assert "p" in outer
        assert isinstance(outer["p"], str)
        assert outer["p"]
    else:
        assert outer.get("encoding") in ("plain", "raw")
        assert "p" in outer


def test_posix_mode_is_owner_rw_when_supported(tmp_path: Path) -> None:
    load_or_create_host_keypair(tmp_path)
    path = host_key_path(tmp_path)
    mode = stat.S_IMODE(path.stat().st_mode)
    if sys.platform == "win32":
        assert path.is_file()
    else:
        assert mode == 0o600


def test_reload_from_raw_32_byte_file(tmp_path: Path) -> None:
    kp = KeyPair.generate()
    path = host_key_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(kp.private)
    loaded = load_or_create_host_keypair(tmp_path)
    assert loaded.public == kp.public


def test_corrupt_host_key_fails_closed(tmp_path: Path) -> None:
    path = host_key_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError):
        load_or_create_host_keypair(tmp_path)


def test_unrecognized_envelope_fails_closed(tmp_path: Path) -> None:
    path = host_key_path(tmp_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"v": 2, "encoding": "mystery"}), encoding="utf-8")
    with pytest.raises(ValueError):
        load_or_create_host_keypair(tmp_path)


def test_keypair_repr_omits_private() -> None:
    kp = KeyPair.generate()
    text = repr(kp)
    assert kp.private.hex() not in text
    assert "private" not in text


def test_load_or_create_does_not_log_private_or_tokens(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    kp = load_or_create_host_keypair(tmp_path)
    load_or_create_host_keypair(tmp_path)
    text = caplog.text
    assert kp.private.hex() not in text
    assert kp.private.decode("latin-1", errors="replace") not in text
    assert "PAIR_SECRET" not in text
