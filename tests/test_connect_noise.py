"""Noise_IK handshake + CipherState family (not a single reproduction string)."""

from __future__ import annotations

import logging

import pytest

from remedy.connect.noise import (
    PROTOCOL_NAME,
    CipherState,
    HandshakeState,
    KeyPair,
    NoiseError,
    encode_nonce,
)
from remedy.connect.record import decrypt_record, encrypt_record


def _pair() -> tuple[HandshakeState, HandshakeState]:
    host = KeyPair.generate()
    phone = KeyPair.generate()
    initiator = HandshakeState(initiator=True, s=phone, rs=host.public)
    responder = HandshakeState(initiator=False, s=host)
    return initiator, responder


def _complete(
    init: HandshakeState,
    resp: HandshakeState,
    *,
    p1: bytes = b"",
    p2: bytes = b"",
) -> tuple[tuple[CipherState, CipherState], tuple[CipherState, CipherState]]:
    msg1 = init.write_message(p1)
    assert resp.read_message(msg1) == p1
    msg2 = resp.write_message(p2)
    assert init.read_message(msg2) == p2
    return init.split(), resp.split()


def test_protocol_name_is_hashed_because_longer_than_hashlen() -> None:
    assert len(PROTOCOL_NAME) > 32
    assert PROTOCOL_NAME == b"Noise_IK_25519_ChaChaPoly_BLAKE2s"


def test_ik_handshake_encrypt_decrypt_both_directions() -> None:
    (send_i, recv_i), (send_r, recv_r) = _complete(*_pair())
    hello = send_i.encrypt_with_ad(b"", b"phone-to-host")
    assert recv_r.decrypt_with_ad(b"", hello) == b"phone-to-host"
    reply = send_r.encrypt_with_ad(b"", b"host-to-phone")
    assert recv_i.decrypt_with_ad(b"", reply) == b"host-to-phone"


def test_ik_handshake_payloads_roundtrip() -> None:
    init, resp = _pair()
    _complete(init, resp, p1=b"from-phone", p2=b"from-host")


@pytest.mark.parametrize(
    "payload",
    [b"", b"x", b"\x00\xff" * 50, b"PAIR_SECRET_never_log_me"],
)
def test_ik_handshake_payload_family(payload: bytes) -> None:
    init, resp = _pair()
    (send_i, recv_i), (send_r, recv_r) = _complete(init, resp, p1=payload, p2=payload)
    ct = send_i.encrypt_with_ad(b"ad-i", payload)
    assert recv_r.decrypt_with_ad(b"ad-i", ct) == payload
    ct2 = send_r.encrypt_with_ad(b"ad-r", payload)
    assert recv_i.decrypt_with_ad(b"ad-r", ct2) == payload


@pytest.mark.parametrize(
    "bad_rs",
    [
        KeyPair.generate().public,
        bytes(32),
        bytes(range(32)),
        KeyPair.generate().public[:-1] + bytes([(KeyPair.generate().public[-1] ^ 0x01)]),
    ],
)
def test_wrong_rs_after_pin_fails(bad_rs: bytes) -> None:
    host = KeyPair.generate()
    phone = KeyPair.generate()
    init = HandshakeState(initiator=True, s=phone, rs=bad_rs)
    resp = HandshakeState(initiator=False, s=host)
    with pytest.raises(NoiseError):
        msg1 = init.write_message(b"pin")
        resp.read_message(msg1)


def test_wrong_rs_length_rejected_at_construct() -> None:
    phone = KeyPair.generate()
    with pytest.raises(ValueError):
        HandshakeState(initiator=True, s=phone, rs=None)
    with pytest.raises(ValueError):
        HandshakeState(initiator=True, s=phone, rs=b"short")
    with pytest.raises(ValueError):
        HandshakeState(initiator=True, s=phone, rs=b"\x00" * 31)
    with pytest.raises(ValueError):
        HandshakeState(initiator=True, s=phone, rs=b"\x00" * 33)


def test_rekey_then_both_directions_still_work() -> None:
    (send_i, recv_i), (send_r, recv_r) = _complete(*_pair())
    for cs in (send_i, recv_i, send_r, recv_r):
        cs.rekey()
        assert cs.nonce() == 0
    ct = send_i.encrypt_with_ad(b"", b"after-rekey")
    assert recv_r.decrypt_with_ad(b"", ct) == b"after-rekey"
    ct2 = send_r.encrypt_with_ad(b"", b"back")
    assert recv_i.decrypt_with_ad(b"", ct2) == b"back"


def test_reused_cipherstate_nonce_decrypt_fails() -> None:
    (send_i, _), (_, recv_r) = _complete(*_pair())
    ct = send_i.encrypt_with_ad(b"", b"once")
    assert recv_r.decrypt_with_ad(b"", ct) == b"once"
    with pytest.raises(NoiseError):
        recv_r.decrypt_with_ad(b"", ct)


def test_tampered_ciphertext_fails_closed_without_advancing_nonce() -> None:
    (send_i, _), (_, recv_r) = _complete(*_pair())
    ct = send_i.encrypt_with_ad(b"", b"auth")
    n_before = recv_r.nonce()
    flipped = bytes([ct[0] ^ 0x01]) + ct[1:]
    with pytest.raises(NoiseError):
        recv_r.decrypt_with_ad(b"", flipped)
    assert recv_r.nonce() == n_before
    assert recv_r.decrypt_with_ad(b"", ct) == b"auth"


def test_truncated_handshake_message_fails() -> None:
    init, resp = _pair()
    msg1 = init.write_message()
    with pytest.raises(NoiseError):
        resp.read_message(msg1[:16])
    with pytest.raises(NoiseError):
        resp.read_message(b"")
    with pytest.raises(NoiseError):
        resp.read_message(msg1[:-1])


def test_wrong_turn_fails() -> None:
    init, resp = _pair()
    with pytest.raises(NoiseError):
        resp.write_message()
    with pytest.raises(NoiseError):
        init.read_message(b"\x00" * 96)


def test_encode_nonce_family() -> None:
    assert encode_nonce(0) == b"\x00" * 12
    assert encode_nonce(1) == b"\x00" * 4 + b"\x01" + b"\x00" * 7
    packed = encode_nonce(0x0102030405060708)
    assert packed[:4] == b"\x00" * 4
    assert packed[4:] == bytes([0x08, 0x07, 0x06, 0x05, 0x04, 0x03, 0x02, 0x01])
    with pytest.raises(NoiseError):
        encode_nonce(-1)


def test_keypair_rejects_wrong_size() -> None:
    with pytest.raises(ValueError):
        KeyPair.from_private(b"short")
    with pytest.raises(ValueError):
        KeyPair.from_private(b"\x00" * 31)
    with pytest.raises(ValueError):
        KeyPair(private=b"\x00" * 32, public=b"\x00" * 16)


def test_handshake_and_records_do_not_log_payloads_or_keys(
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    host = KeyPair.generate()
    phone = KeyPair.generate()
    marker = b"PAIR_SECRET_token_must_never_appear"
    init = HandshakeState(initiator=True, s=phone, rs=host.public)
    resp = HandshakeState(initiator=False, s=host)
    (send_i, recv_i), (send_r, recv_r) = _complete(init, resp, p1=marker, p2=marker)
    blob = encrypt_record(send_i, marker)
    assert decrypt_record(recv_r, blob) == marker
    blob2 = encrypt_record(send_r, marker)
    assert decrypt_record(recv_i, blob2) == marker
    text = caplog.text
    assert marker.decode() not in text
    assert host.private.hex() not in text
    assert phone.private.hex() not in text
    assert "PAIR_SECRET" not in text
