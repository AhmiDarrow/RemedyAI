"""Noise_IK_25519_ChaChaPoly_BLAKE2s (revision 34) for Grove Connect.

No payload, key, or shared-secret material is logged.
"""

from __future__ import annotations

import hashlib
import hmac
import struct
from collections.abc import Callable
from dataclasses import dataclass

from nacl import bindings as nacl_bindings
from nacl import utils as nacl_utils
from nacl.exceptions import CryptoError

PROLOGUE = b"remedy-connect/1"
PROTOCOL_NAME = b"Noise_IK_25519_ChaChaPoly_BLAKE2s"

DHLEN = 32
HASHLEN = 32
CIPHERKEYLEN = 32
TAGLEN = 16
MAX_NONCE = (1 << 64) - 1  # reserved for Rekey(); encrypt/decrypt must not use it
_ZERO_KEY_CHECK = b"\x00" * DHLEN

# IK pattern (initiator knows responder static from QR ``hp=``).
_IK_MESSAGES: tuple[tuple[str, ...], ...] = (
    ("e", "es", "s", "ss"),
    ("e", "ee", "se"),
)


class NoiseError(Exception):
    """Handshake or AEAD failure. Messages never contain key or payload bytes."""


def _hash(data: bytes) -> bytes:
    return hashlib.blake2s(data, digest_size=HASHLEN).digest()


def _hmac(key: bytes, data: bytes) -> bytes:
    return hmac.new(key, data, lambda: hashlib.blake2s(digest_size=HASHLEN)).digest()


def _hkdf(ck: bytes, ikm: bytes, outputs: int) -> tuple[bytes, ...]:
    """Noise HKDF: temp_key = HMAC(ck, ikm); chained 0x01 / 0x02 / 0x03."""
    if outputs not in (2, 3):
        raise ValueError("Noise HKDF yields 2 or 3 outputs")
    temp_key = _hmac(ck, ikm)
    out1 = _hmac(temp_key, b"\x01")
    out2 = _hmac(temp_key, out1 + b"\x02")
    if outputs == 2:
        return out1, out2
    out3 = _hmac(temp_key, out2 + b"\x03")
    return out1, out2, out3


def encode_nonce(n: int) -> bytes:
    """12-byte ChaChaPoly nonce: 4 zero bytes || uint64le(n)."""
    if n < 0 or n > MAX_NONCE:
        raise NoiseError("nonce out of range")
    return b"\x00\x00\x00\x00" + struct.pack("<Q", n)


def _dh(local: KeyPair, remote_public: bytes) -> bytes:
    if len(remote_public) != DHLEN:
        raise NoiseError("invalid public key")
    try:
        shared = nacl_bindings.crypto_scalarmult(local.private, remote_public)
    except (CryptoError, RuntimeError, ValueError) as exc:
        raise NoiseError("invalid DH result") from exc
    if shared == _ZERO_KEY_CHECK:
        raise NoiseError("invalid DH result")
    return shared


def _aead_encrypt(key: bytes, n: int, ad: bytes, plaintext: bytes) -> bytes:
    try:
        return nacl_bindings.crypto_aead_chacha20poly1305_ietf_encrypt(
            plaintext, ad, encode_nonce(n), key
        )
    except (CryptoError, ValueError, RuntimeError) as exc:
        raise NoiseError("encrypt failed") from exc


def _aead_decrypt(key: bytes, n: int, ad: bytes, ciphertext: bytes) -> bytes:
    if len(ciphertext) < TAGLEN:
        raise NoiseError("truncated ciphertext")
    try:
        return nacl_bindings.crypto_aead_chacha20poly1305_ietf_decrypt(
            ciphertext, ad, encode_nonce(n), key
        )
    except (CryptoError, ValueError, RuntimeError) as exc:
        raise NoiseError("decrypt failed") from exc


@dataclass(frozen=True)
class KeyPair:
    private: bytes
    public: bytes

    def __post_init__(self) -> None:
        if len(self.private) != DHLEN or len(self.public) != DHLEN:
            raise ValueError("X25519 keys must be 32 bytes")

    def __repr__(self) -> str:
        return f"KeyPair(public={self.public.hex()[:16]}…)"

    @classmethod
    def generate(cls) -> KeyPair:
        return cls.from_private(nacl_utils.random(DHLEN))

    @classmethod
    def from_private(cls, sk: bytes) -> KeyPair:
        if len(sk) != DHLEN:
            raise ValueError("X25519 private key must be 32 bytes")
        pk = nacl_bindings.crypto_scalarmult_base(sk)
        return cls(private=sk, public=pk)


class CipherState:
    """Noise CipherState: IETF ChaCha20-Poly1305, nonce strictly increasing."""

    def __init__(self, key: bytes | None = None) -> None:
        if key is not None and len(key) != CIPHERKEYLEN:
            raise ValueError("cipher key must be 32 bytes")
        self._k: bytes | None = key
        self._n: int = 0

    def has_key(self) -> bool:
        return self._k is not None

    def nonce(self) -> int:
        return self._n

    def encrypt_with_ad(self, ad: bytes, plaintext: bytes) -> bytes:
        if self._k is None:
            return plaintext
        if self._n >= MAX_NONCE:
            raise NoiseError("nonce exhausted")
        out = _aead_encrypt(self._k, self._n, ad, plaintext)
        self._n += 1
        return out

    def decrypt_with_ad(self, ad: bytes, ciphertext: bytes) -> bytes:
        if self._k is None:
            return ciphertext
        if self._n >= MAX_NONCE:
            raise NoiseError("nonce exhausted")
        # Authentication failure must not increment n (Noise §5.1).
        pt = _aead_decrypt(self._k, self._n, ad, ciphertext)
        self._n += 1
        return pt

    def rekey(self) -> None:
        """REKEY(k) = first 32 bytes of ENCRYPT(k, 2^64-1, empty AD, 32 zero bytes).

        ``n`` is reset to 0 so the new key starts a fresh nonce sequence.
        """
        if self._k is None:
            raise NoiseError("cannot rekey an empty CipherState")
        zeros = b"\x00" * CIPHERKEYLEN
        out = _aead_encrypt(self._k, MAX_NONCE, b"", zeros)
        self._k = out[:CIPHERKEYLEN]
        self._n = 0


class HandshakeState:
    """Noise_IK initiator (phone) or responder (host)."""

    def __init__(
        self,
        *,
        initiator: bool,
        s: KeyPair,
        rs: bytes | None = None,
        prologue: bytes = PROLOGUE,
    ) -> None:
        self._initiator = initiator
        self._s = s
        self._e: KeyPair | None = None
        self._rs: bytes | None = rs
        self._re: bytes | None = None
        self._msg_i = 0
        self._done = False
        self._split_done = False

        if initiator:
            if rs is None or len(rs) != DHLEN:
                raise ValueError("IK initiator requires the 32-byte host static public")
        elif rs is not None and len(rs) != DHLEN:
            raise ValueError("remote static public must be 32 bytes")

        if len(PROTOCOL_NAME) <= HASHLEN:
            self._h = PROTOCOL_NAME.ljust(HASHLEN, b"\x00")
        else:
            self._h = _hash(PROTOCOL_NAME)
        self._ck = self._h
        self._cs = CipherState(None)
        self._mix_hash(prologue)
        # Pre-message: <- s  (responder static, known to initiator from QR).
        if initiator:
            self._mix_hash(rs or b"")
        else:
            self._mix_hash(s.public)

    def write_message(self, payload: bytes = b"") -> bytes:
        if self._done:
            raise NoiseError("handshake is complete")
        if self._write_turn() is False:
            raise NoiseError("not this role's turn to write")
        tokens = _IK_MESSAGES[self._msg_i]
        buf = bytearray()
        for token in tokens:
            buf.extend(self._write_token(token))
        buf.extend(self._encrypt_and_hash(payload))
        self._advance()
        return bytes(buf)

    def read_message(self, message: bytes) -> bytes:
        if self._done:
            raise NoiseError("handshake is complete")
        if self._write_turn() is True:
            raise NoiseError("not this role's turn to read")
        tokens = _IK_MESSAGES[self._msg_i]
        pos = 0

        def take(n: int) -> bytes:
            nonlocal pos
            if n < 0 or pos + n > len(message):
                raise NoiseError("truncated handshake message")
            chunk = message[pos : pos + n]
            pos += n
            return chunk

        for token in tokens:
            self._read_token(token, take)
        payload = self._decrypt_and_hash(message[pos:])
        self._advance()
        return payload

    def split(self) -> tuple[CipherState, CipherState]:
        if not self._done:
            raise NoiseError("handshake is not complete")
        if self._split_done:
            raise NoiseError("handshake already split")
        k1, k2 = _hkdf(self._ck, b"", 2)
        self._split_done = True
        c1 = CipherState(k1)
        c2 = CipherState(k2)
        if self._initiator:
            return c1, c2
        return c2, c1

    def _write_turn(self) -> bool:
        # Even messages: initiator writes; odd: responder writes.
        return (self._msg_i % 2 == 0) == self._initiator

    def _advance(self) -> None:
        self._msg_i += 1
        if self._msg_i >= len(_IK_MESSAGES):
            self._done = True

    def _mix_hash(self, data: bytes) -> None:
        self._h = _hash(self._h + data)

    def _mix_key(self, ikm: bytes) -> None:
        self._ck, temp_k = _hkdf(self._ck, ikm, 2)
        self._cs = CipherState(temp_k)

    def _encrypt_and_hash(self, plaintext: bytes) -> bytes:
        ct = self._cs.encrypt_with_ad(self._h, plaintext)
        self._mix_hash(ct)
        return ct

    def _decrypt_and_hash(self, ciphertext: bytes) -> bytes:
        pt = self._cs.decrypt_with_ad(self._h, ciphertext)
        self._mix_hash(ciphertext)
        return pt

    def _ct_len(self, plaintext_len: int) -> int:
        extra = TAGLEN if self._cs.has_key() else 0
        return plaintext_len + extra

    def _write_token(self, token: str) -> bytes:
        if token == "e":
            self._e = KeyPair.generate()
            self._mix_hash(self._e.public)
            return self._e.public
        if token == "s":
            return self._encrypt_and_hash(self._s.public)
        self._mix_dh(token)
        return b""

    def _read_token(self, token: str, take: Callable[[int], bytes]) -> None:
        if token == "e":
            self._re = take(DHLEN)
            self._mix_hash(self._re)
            return
        if token == "s":
            raw = take(self._ct_len(DHLEN))
            rs = self._decrypt_and_hash(raw)
            if len(rs) != DHLEN:
                raise NoiseError("invalid remote static")
            self._rs = rs
            return
        self._mix_dh(token)

    def _mix_dh(self, token: str) -> None:
        if token == "ee":
            if self._e is None or self._re is None:
                raise NoiseError("missing ephemeral for ee")
            self._mix_key(_dh(self._e, self._re))
            return
        if token == "es":
            if self._initiator:
                if self._e is None or self._rs is None:
                    raise NoiseError("missing keys for es")
                self._mix_key(_dh(self._e, self._rs))
            else:
                if self._re is None:
                    raise NoiseError("missing keys for es")
                self._mix_key(_dh(self._s, self._re))
            return
        if token == "se":
            if self._initiator:
                if self._re is None:
                    raise NoiseError("missing keys for se")
                self._mix_key(_dh(self._s, self._re))
            else:
                if self._e is None or self._rs is None:
                    raise NoiseError("missing keys for se")
                self._mix_key(_dh(self._e, self._rs))
            return
        if token == "ss":
            if self._rs is None:
                raise NoiseError("missing keys for ss")
            self._mix_key(_dh(self._s, self._rs))
            return
        raise NoiseError("unknown handshake token")
