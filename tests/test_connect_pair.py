"""Pairing: expired / reused QR, device cap, no token in QR, audit hygiene."""

from __future__ import annotations

import pytest

from remedy.connect.audit import audit_path
from remedy.connect.pair import complete_pair, parse_pair_secret, start_pair
from remedy.connect.store import active_device_count, list_devices


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("REMEDY_HOME", str(tmp_path))
    return tmp_path


def _qr(bind_host: str = "127.0.0.1", bind_port: int = 7401) -> str:
    return start_pair(loopback=True, bind_host=bind_host, bind_port=bind_port)


def test_start_pair_refuses_non_loopback(home):
    with pytest.raises(PermissionError, match="loopback"):
        start_pair(loopback=False, bind_host="127.0.0.1", bind_port=7401)


def test_qr_has_no_local_api_token_or_bearer(home, monkeypatch):
    monkeypatch.setenv("REMEDY_API_KEY", "not-a-portal-secret-dummy-key")
    qr = _qr()
    low = qr.lower()
    assert "remedy-connect/1" in qr
    assert "hp=" in qr
    assert "ps=" in qr
    assert "lan=127.0.0.1:7401" in qr
    assert "exp=" in qr
    assert "local_api_token" not in low
    assert "bearer" not in low
    assert "api_key" not in low
    assert "authorization" not in low
    assert "not-a-portal-secret-dummy-key" not in qr


def test_expired_qr_fail_closed(home, monkeypatch):
    from remedy.connect import pair as pair_mod

    now = [1_700_000_000.0]
    monkeypatch.setattr(pair_mod, "_now", lambda: now[0])
    qr = _qr()
    secret = parse_pair_secret(qr)
    now[0] = 1_700_000_000.0 + 61
    with pytest.raises(ValueError, match="expired"):
        complete_pair(secret, b"\x11" * 32, "phone")
    assert active_device_count() == 0


def test_reused_qr_fail_closed(home):
    qr = _qr()
    secret = parse_pair_secret(qr)
    device_id = complete_pair(secret, b"\x22" * 32, "phone-a")
    assert device_id
    with pytest.raises(ValueError, match="reused"):
        complete_pair(secret, b"\x33" * 32, "phone-b")
    assert active_device_count() == 1


def test_fourth_device_refused(home):
    for i in range(3):
        qr = _qr()
        complete_pair(parse_pair_secret(qr), bytes([i + 1]) * 32, f"phone-{i}")
    assert active_device_count() == 3
    qr = _qr()
    with pytest.raises(ValueError, match="device limit"):
        complete_pair(parse_pair_secret(qr), b"\x44" * 32, "phone-3")
    assert active_device_count() == 3
    names = {d["name"] for d in list_devices(include_revoked=False)}
    assert names == {"phone-0", "phone-1", "phone-2"}


def test_pair_envelope_secret_may_contain_nul(home):
    from remedy.connect.pair import pair_payload, parse_handshake_payload

    secret = b"\x00" + b"\x41" * 30 + b"\x00"
    payload = pair_payload(secret, "phone")
    kind, fields = parse_handshake_payload(payload)
    assert kind == "pair"
    assert fields["secret"] == secret
    assert fields["name"] == "phone"


def test_raw_32_byte_handshake_payload_pairs(home):
    from remedy.connect.pair import parse_handshake_payload

    qr = _qr()
    secret = parse_pair_secret(qr)
    kind, fields = parse_handshake_payload(secret)
    assert kind == "pair"
    assert fields["secret"] == secret
    device_id = complete_pair(fields["secret"], b"\x77" * 32, fields["name"])
    assert device_id
    assert active_device_count() == 1


def test_pair_payload_envelope_still_pairs(home):
    from remedy.connect.pair import pair_payload, parse_handshake_payload

    qr = _qr()
    secret = parse_pair_secret(qr)
    kind, fields = parse_handshake_payload(pair_payload(secret, "desk-phone"))
    assert kind == "pair"
    assert fields["name"] == "desk-phone"
    device_id = complete_pair(fields["secret"], b"\x88" * 32, fields["name"])
    assert device_id


def test_pair_secret_not_in_audit_or_log_strings(home, caplog):
    qr = _qr()
    secret = parse_pair_secret(qr)
    b64 = [ln[3:] for ln in qr.splitlines() if ln.startswith("ps=")][0]
    complete_pair(secret, b"\x55" * 32, "kitchen-phone")
    text = ""
    path = audit_path(home)
    if path.is_file():
        text = path.read_text(encoding="utf-8")
    assert "pair" in text
    assert "kitchen-phone" in text
    assert secret.hex() not in text
    assert b64 not in text
    assert "local_api_token" not in text
    assert "Bearer" not in text
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert b64 not in joined
    assert secret.hex() not in joined
