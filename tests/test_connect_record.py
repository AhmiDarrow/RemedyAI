"""Record codec family: framing, size cap, replay, asyncio read."""

from __future__ import annotations

import asyncio
import struct

import pytest

from remedy.connect.noise import (
    CipherState,
    HandshakeState,
    KeyPair,
    NoiseError,
    encode_nonce,
)
from remedy.connect.record import (
    MAX_PLAINTEXT,
    MAX_RECORD,
    NONCE_LEN,
    decrypt_record,
    encrypt_record,
    pack_record,
    read_record,
    unpack_record,
)


def _transport() -> tuple[CipherState, CipherState]:
    host = KeyPair.generate()
    phone = KeyPair.generate()
    init = HandshakeState(initiator=True, s=phone, rs=host.public)
    resp = HandshakeState(initiator=False, s=host)
    msg1 = init.write_message()
    resp.read_message(msg1)
    msg2 = resp.write_message()
    init.read_message(msg2)
    send_i, _recv_i = init.split()
    _send_r, recv_r = resp.split()
    return send_i, recv_r


@pytest.mark.parametrize(
    "nonce,ct",
    [
        (b"\x00" * 12, b""),
        (b"\x00" * 4 + b"\x01" + b"\x00" * 7, b"x"),
        (b"\xff" * 12, b"\x00" * 16),
        (bytes(range(12)), b"cipher" + b"\xab" * 20),
    ],
)
def test_pack_unpack_roundtrip(nonce: bytes, ct: bytes) -> None:
    blob = pack_record(nonce, ct)
    got_n, got_ct = unpack_record(blob)
    assert got_n == nonce
    assert got_ct == ct
    (length,) = struct.unpack("!I", blob[:4])
    assert length == NONCE_LEN + len(ct)
    assert blob[4:16] == nonce


@pytest.mark.parametrize("bad_nonce", [b"", b"\x00" * 11, b"\x00" * 13, b"\x00" * 16])
def test_pack_rejects_nonce_length_family(bad_nonce: bytes) -> None:
    with pytest.raises(ValueError):
        pack_record(bad_nonce, b"ct")


@pytest.mark.parametrize(
    "blob",
    [
        b"",
        b"\x00\x00",
        b"\x00\x00\x00\x0c",  # claims 12 bytes, no body
        struct.pack("!I", 12) + b"\x00" * 11,
        struct.pack("!I", 13) + b"\x00" * 12,  # length mismatch
    ],
)
def test_unpack_truncated_family(blob: bytes) -> None:
    with pytest.raises(ValueError):
        unpack_record(blob)


def test_record_at_max_accepted() -> None:
    ct = b"\x00" * (MAX_RECORD - NONCE_LEN)
    blob = pack_record(b"\x00" * NONCE_LEN, ct)
    nonce, got = unpack_record(blob)
    assert nonce == b"\x00" * NONCE_LEN
    assert got == ct


@pytest.mark.parametrize(
    "oversize",
    [
        MAX_RECORD + 1,
        MAX_RECORD + 16,
        MAX_RECORD + 65535,
    ],
)
def test_record_over_64kib_rejected(oversize: int) -> None:
    ct = b"\x00" * (oversize - NONCE_LEN)
    with pytest.raises(ValueError):
        pack_record(b"\x00" * NONCE_LEN, ct)
    claimed = struct.pack("!I", oversize) + b"\x00" * 20
    with pytest.raises(ValueError):
        unpack_record(claimed)


def test_unpack_rejects_length_field_above_max_even_if_body_short() -> None:
    blob = struct.pack("!I", MAX_RECORD + 1) + b"\x00" * (NONCE_LEN + 16)
    with pytest.raises(ValueError):
        unpack_record(blob)


def test_encrypt_decrypt_record_roundtrip() -> None:
    send_i, recv_r = _transport()
    blob = encrypt_record(send_i, b"hello-record")
    assert decrypt_record(recv_r, blob) == b"hello-record"


@pytest.mark.parametrize("plain", [b"", b"a", b"\x00" * 100, b"token-never-log"])
def test_encrypt_record_payload_family(plain: bytes) -> None:
    send_i, recv_r = _transport()
    blob = encrypt_record(send_i, plain)
    nonce, _ct = unpack_record(blob)
    assert nonce == encode_nonce(0)
    assert decrypt_record(recv_r, blob) == plain


def test_reused_record_nonce_replay_fails() -> None:
    send_i, recv_r = _transport()
    blob = encrypt_record(send_i, b"first")
    assert decrypt_record(recv_r, blob) == b"first"
    with pytest.raises(NoiseError):
        decrypt_record(recv_r, blob)


def test_rewound_nonce_in_packed_record_fails() -> None:
    send_i, recv_r = _transport()
    first = encrypt_record(send_i, b"a")
    second = encrypt_record(send_i, b"b")
    assert decrypt_record(recv_r, first) == b"a"
    assert decrypt_record(recv_r, second) == b"b"
    with pytest.raises(NoiseError):
        decrypt_record(recv_r, first)


def test_future_nonce_fails_closed() -> None:
    send_i, recv_r = _transport()
    blob = encrypt_record(send_i, b"x")
    nonce, ct = unpack_record(blob)
    future = pack_record(encode_nonce(5), ct)
    assert nonce == encode_nonce(0)
    with pytest.raises(NoiseError):
        decrypt_record(recv_r, future)


def test_nonzero_nonce_prefix_fails() -> None:
    send_i, recv_r = _transport()
    blob = encrypt_record(send_i, b"x")
    _nonce, ct = unpack_record(blob)
    bad = pack_record(b"\x01" + b"\x00" * 11, ct)
    with pytest.raises(NoiseError):
        decrypt_record(recv_r, bad)


def test_encrypt_record_oversize_plaintext_rejected_without_consuming_nonce() -> None:
    send_i, recv_r = _transport()
    n_before = send_i.nonce()
    with pytest.raises(ValueError):
        encrypt_record(send_i, b"\x00" * (MAX_PLAINTEXT + 1))
    assert send_i.nonce() == n_before
    blob = encrypt_record(send_i, b"ok")
    assert decrypt_record(recv_r, blob) == b"ok"


def test_encrypt_record_max_plaintext_accepted() -> None:
    send_i, recv_r = _transport()
    plain = b"Z" * MAX_PLAINTEXT
    blob = encrypt_record(send_i, plain)
    assert decrypt_record(recv_r, blob) == plain


@pytest.mark.asyncio
async def test_read_record_roundtrip() -> None:
    nonce = b"\x00" * 4 + b"\x02" + b"\x00" * 7
    ct = b"async-body" + b"\x00" * 16
    blob = pack_record(nonce, ct)
    reader = asyncio.StreamReader()
    reader.feed_data(blob)
    reader.feed_eof()
    got_n, got_ct = await read_record(reader)
    assert got_n == nonce
    assert got_ct == ct


@pytest.mark.asyncio
async def test_read_record_rejects_oversize_length_field() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack("!I", MAX_RECORD + 1))
    reader.feed_eof()
    with pytest.raises(ValueError):
        await read_record(reader)


@pytest.mark.asyncio
async def test_read_record_truncated_body() -> None:
    reader = asyncio.StreamReader()
    reader.feed_data(struct.pack("!I", 20) + b"\x00" * 4)
    reader.feed_eof()
    with pytest.raises((asyncio.IncompleteReadError, ValueError)):
        await read_record(reader)
