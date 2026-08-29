"""Length-prefixed Noise transport records: u32be | nonce12 | ciphertext.

The length field is the size of ``nonce12 || ciphertext`` and must be
``<= MAX_RECORD`` (64 KiB). No compression.
"""

from __future__ import annotations

import asyncio
import struct

from remedy.connect.noise import TAGLEN, CipherState, NoiseError, encode_nonce

MAX_RECORD = 65536
NONCE_LEN = 12
MAX_PLAINTEXT = MAX_RECORD - NONCE_LEN - TAGLEN


def pack_record(nonce12: bytes, ciphertext: bytes) -> bytes:
    if len(nonce12) != NONCE_LEN:
        raise ValueError("record nonce must be 12 bytes")
    body = nonce12 + ciphertext
    if len(body) > MAX_RECORD:
        raise ValueError("record exceeds 64 KiB")
    return struct.pack("!I", len(body)) + body


def unpack_record(blob: bytes) -> tuple[bytes, bytes]:
    if len(blob) < 4 + NONCE_LEN:
        raise ValueError("truncated record")
    (length,) = struct.unpack("!I", blob[:4])
    if length > MAX_RECORD:
        raise ValueError("record exceeds 64 KiB")
    if length < NONCE_LEN:
        raise ValueError("record too short")
    if len(blob) != 4 + length:
        raise ValueError("record length mismatch")
    body = blob[4:]
    return body[:NONCE_LEN], body[NONCE_LEN:]


async def read_record(reader: asyncio.StreamReader) -> tuple[bytes, bytes]:
    header = await reader.readexactly(4)
    (length,) = struct.unpack("!I", header)
    if length > MAX_RECORD:
        raise ValueError("record exceeds 64 KiB")
    if length < NONCE_LEN:
        raise ValueError("record too short")
    body = await reader.readexactly(length)
    return body[:NONCE_LEN], body[NONCE_LEN:]


def encrypt_record(cs: CipherState, plaintext: bytes) -> bytes:
    """Encrypt with AD=b'', pack u32be|nonce12|ct. Rekey caller decides."""
    if len(plaintext) > MAX_PLAINTEXT:
        raise ValueError("record exceeds 64 KiB")
    nonce12 = encode_nonce(cs.nonce())
    ciphertext = cs.encrypt_with_ad(b"", plaintext)
    return pack_record(nonce12, ciphertext)


def decrypt_record(cs: CipherState, blob: bytes) -> bytes:
    nonce12, ciphertext = unpack_record(blob)
    expected = encode_nonce(cs.nonce())
    if nonce12 != expected:
        raise NoiseError("record nonce replay or out of order")
    return cs.decrypt_with_ad(b"", ciphertext)
