"""Capture Noise_IK fixed-key byte vectors from live Python before any Go port.

Mirrors Android ``NoiseIkTest`` snow hex where present. Writes JSON+hex under
``tests/fixtures/connect/``. Run:

    uv run python -m tests.harness.connect_fixture_capture

Not part of the default pytest collect for side effects — regeneration is
explicit; ``tests/test_connect_noise_fixtures.py`` only *loads* and re-verifies.
"""

from __future__ import annotations

import hmac
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from remedy.connect.noise import (
    PROLOGUE,
    PROTOCOL_NAME,
    HandshakeState,
    KeyPair,
    _hash,
)

FIXTURE_DIR = Path(__file__).resolve().parents[1] / "fixtures" / "connect"

# --- snow crate / Android NoiseIkTest.snowVectorIkChaChaBlake2s ---
SNOW_INIT_STATIC = bytes.fromhex(
    "b7e117ce8ede06ceb89500799a3778d097fc54a3f90bea744493dfc24ec21f32"
)
SNOW_INIT_EPH = bytes.fromhex(
    "cc95b4ccc4912c5a52c8d2f6b808e13712392c4468f4e3f02a7d2d1590cb9178"
)
SNOW_INIT_RS = bytes.fromhex(
    "ea82fd2e81d1285f1b2029e46ca7bcaeeeafed15396d002bd434624a4d580655"
)
SNOW_RESP_STATIC = bytes.fromhex(
    "e3187f5c10734e934be3a73c398c7fae07ba3e0cde2fcd9ab03a1c93dcae10f8"
)
SNOW_RESP_EPH = bytes.fromhex(
    "587f83fe736432043d2665fbd47b0506b2cd103b6ba8577f72e117c7ffeb5105"
)
SNOW_PROLOGUE = bytes.fromhex(
    "5468657265206973206e6f20726967687420616e642077726f6e672e20546865"
    "72652773206f6e6c792066756e20616e6420626f72696e672e"
)
SNOW_MSG0_PAYLOAD = bytes.fromhex(
    "910ef4a7c04090f66403fcd8ffaed066e70ed38b576792c4a554cc5016fe5120"
)
SNOW_MSG0_CT_ANDROID = bytes.fromhex(
    "df44a475167152d3e4767b9ad5b28468fb8bd6aaa0b0e7181afd87ede7a8c971"
    "e818de301c97393bfa71ac250becfc8acef64a969e039f407fe44c5ffc3d66d9"
    "4ff57627ad4cc53882a1b2993babac3940edd1eaff34a3d3f08c53570166b3d2"
    "9d06bd5de3cfb9b85b66001a60a5bb98d72b80da9485655f86db72b46f745c4e"
)
SNOW_MSG1_PAYLOAD = bytes.fromhex(
    "3ce1e4d6e5f02bfeea1d6620cbf1473b5f55372d6954e98d3e12b6ffd04879d5"
)
SNOW_MSG1_CT_ANDROID = bytes.fromhex(
    "2818be348d38de5f9e3ef545c62e8e276c12e2a64410801a0284e02dfb0d450a"
    "848d1001977ef31dfe04b5c824acc04c7bda8490eab6cb6aaa844c4ddf0fda83"
    "ec84ed26cd6bee22fab17360b73de5b8"
)
SNOW_MSG2_PAYLOAD = bytes.fromhex(
    "31e7554ebce419a76bf5cd464e00594ccf55bdc09c234c450850d26ab164238c"
)
SNOW_MSG2_CT_ANDROID = bytes.fromhex(
    "89545eb9b2477d482156692214def7d5aecc00b9244b4435bdb768173841d2a2"
    "a2e52bcee32d64fe938c87594b17ee69"
)
SNOW_MSG3_PAYLOAD = bytes.fromhex(
    "794f643ba08cc7ee7aa39bc055560a0850dbc77bf34d8ab22019bf160ab2c573"
)
SNOW_MSG3_CT_ANDROID = bytes.fromhex(
    "fe35983c049b535cf9ca61cfad5b48e461da90c7c51cb5b873be10e58ae34a38"
    "4aaebba7f96958b0ef0ac043c5e9c622"
)

# Android debugHash intermediate expectations
ANDROID_H0 = bytes.fromhex(
    "bbea022b948cf3bc5857d70804229179e1116bc40cb8cc074835349c464bca36"
)
ANDROID_H_PROLOGUE = bytes.fromhex(
    "5bb156750593ca8a20dec81f2208ac09fe9639e6989e5f56bcff5e5a198361f3"
)
ANDROID_H_RS = bytes.fromhex(
    "b2e7409ee8575150d9b8d176cc326a68c7581746a52147a172b8a73882aa60a2"
)
ANDROID_H_E = bytes.fromhex(
    "62282308202dee87b14acbf965f46af4718f7e119bf791651f4300c5de8ecc7b"
)
ANDROID_K_AFTER_ES = bytes.fromhex(
    "a7b2e46735a1aac6374af349a6108212f1de879c35ccd1ae445a8c3e20462015"
)
ANDROID_ENC_S = bytes.fromhex(
    "e818de301c97393bfa71ac250becfc8acef64a969e039f407fe44c5ffc3d66d9"
    "4ff57627ad4cc53882a1b2993babac39"
)

# --- Remedy product prologue + pair-secret (mirrors Android pairSecret test PS) ---
REMEDY_PHONE_STATIC = bytes.fromhex("11" * 32)
REMEDY_HOST_STATIC = bytes.fromhex("22" * 32)
REMEDY_PHONE_EPH = bytes.fromhex("33" * 32)
REMEDY_HOST_EPH = bytes.fromhex("44" * 32)
REMEDY_PAIR_SECRET = bytes([7] * 32)
REMEDY_WRONG_PS = bytes([9] * 32)
REMEDY_MSG2_PLAIN = b"phone-to-host"
REMEDY_MSG3_PLAIN = b"host-to-phone"


def fixture_dir() -> Path:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    return FIXTURE_DIR


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat()


def _hx(data: bytes) -> str:
    return data.hex()


def _write(name: str, payload: dict[str, Any]) -> Path:
    path = fixture_dir() / name
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return path


def _handshake(
    *,
    init_static: bytes,
    init_eph: bytes,
    resp_static: bytes,
    resp_eph: bytes,
    prologue: bytes,
    payload0: bytes,
    payload1: bytes = b"",
) -> dict[str, Any]:
    host = KeyPair.from_private(resp_static)
    phone = KeyPair.from_private(init_static)
    init = HandshakeState(
        initiator=True, s=phone, rs=host.public, prologue=prologue
    )
    resp = HandshakeState(initiator=False, s=host, prologue=prologue)
    init.set_ephemeral_for_test(init_eph)
    resp.set_ephemeral_for_test(resp_eph)

    msg0 = init.write_message(payload0)
    got0 = resp.read_message(msg0)
    if got0 != payload0:
        raise RuntimeError("msg0 payload mismatch after roundtrip")
    msg1 = resp.write_message(payload1)
    got1 = init.read_message(msg1)
    if got1 != payload1:
        raise RuntimeError("msg1 payload mismatch after roundtrip")

    send_i, recv_i = init.split()
    send_r, recv_r = resp.split()
    return {
        "init_static_priv_hex": _hx(init_static),
        "init_static_pub_hex": _hx(phone.public),
        "init_eph_priv_hex": _hx(init_eph),
        "init_eph_pub_hex": _hx(KeyPair.from_private(init_eph).public),
        "resp_static_priv_hex": _hx(resp_static),
        "resp_static_pub_hex": _hx(host.public),
        "resp_eph_priv_hex": _hx(resp_eph),
        "resp_eph_pub_hex": _hx(KeyPair.from_private(resp_eph).public),
        "prologue_hex": _hx(prologue),
        "prologue_utf8": prologue.decode("utf-8", errors="replace"),
        "msg0_payload_hex": _hx(payload0),
        "msg0_ciphertext_hex": _hx(msg0),
        "msg1_payload_hex": _hx(payload1),
        "msg1_ciphertext_hex": _hx(msg1),
        "_send_i": send_i,
        "_recv_i": recv_i,
        "_send_r": send_r,
        "_recv_r": recv_r,
    }


def capture_debug_hash() -> dict[str, Any]:
    """Android NoiseIkTest.debugHash intermediates (snow prologue)."""
    from remedy.connect.noise import _aead_encrypt, _dh, _hkdf

    h0 = _hash(PROTOCOL_NAME)
    h1 = _hash(h0 + SNOW_PROLOGUE)
    h2 = _hash(h1 + SNOW_INIT_RS)
    e_pub = KeyPair.from_private(SNOW_INIT_EPH).public
    h3 = _hash(h2 + e_pub)
    dh = _dh(KeyPair.from_private(SNOW_INIT_EPH), SNOW_INIT_RS)
    _ck, k = _hkdf(h0, dh, 2)
    s_pub = KeyPair.from_private(SNOW_INIT_STATIC).public
    enc_s = _aead_encrypt(k, 0, h3, s_pub)

    matched = (
        h0 == ANDROID_H0
        and h1 == ANDROID_H_PROLOGUE
        and h2 == ANDROID_H_RS
        and h3 == ANDROID_H_E
        and k == ANDROID_K_AFTER_ES
        and enc_s == ANDROID_ENC_S
    )
    return {
        "name": "android_debug_hash",
        "notes": (
            "Intermediate hash/AEAD steps from Android NoiseIkTest.debugHash "
            "(snow literary prologue). Confirms Python BLAKE2s/HKDF/AEAD match."
        ),
        "protocol_name_hex": _hx(PROTOCOL_NAME),
        "h0_hex": _hx(h0),
        "h_prologue_hex": _hx(h1),
        "h_rs_hex": _hx(h2),
        "h_e_hex": _hx(h3),
        "k_after_es_hex": _hx(k),
        "enc_s_hex": _hx(enc_s),
        "android_hex_matched_python": matched,
    }


def capture_snow_ik() -> dict[str, Any]:
    hs = _handshake(
        init_static=SNOW_INIT_STATIC,
        init_eph=SNOW_INIT_EPH,
        resp_static=SNOW_RESP_STATIC,
        resp_eph=SNOW_RESP_EPH,
        prologue=SNOW_PROLOGUE,
        payload0=SNOW_MSG0_PAYLOAD,
        payload1=SNOW_MSG1_PAYLOAD,
    )
    send_i = hs.pop("_send_i")
    recv_i = hs.pop("_recv_i")
    send_r = hs.pop("_send_r")
    recv_r = hs.pop("_recv_r")

    msg2_ct = send_i.encrypt_with_ad(b"", SNOW_MSG2_PAYLOAD)
    msg2_pt = recv_r.decrypt_with_ad(b"", msg2_ct)
    msg3_ct = send_r.encrypt_with_ad(b"", SNOW_MSG3_PAYLOAD)
    msg3_pt = recv_i.decrypt_with_ad(b"", msg3_ct)
    if msg2_pt != SNOW_MSG2_PAYLOAD or msg3_pt != SNOW_MSG3_PAYLOAD:
        raise RuntimeError("snow post-split roundtrip failed")

    android_match = (
        bytes.fromhex(hs["msg0_ciphertext_hex"]) == SNOW_MSG0_CT_ANDROID
        and bytes.fromhex(hs["msg1_ciphertext_hex"]) == SNOW_MSG1_CT_ANDROID
        and msg2_ct == SNOW_MSG2_CT_ANDROID
        and msg3_ct == SNOW_MSG3_CT_ANDROID
        and KeyPair.from_private(SNOW_RESP_STATIC).public == SNOW_INIT_RS
    )
    return {
        "name": "snow_ik_chacha_blake2s",
        "notes": (
            "snow tests/vectors — Noise_IK_25519_ChaChaPoly_BLAKE2s. "
            "Byte-identical to Android NoiseIkTest.snowVectorIkChaChaBlake2s."
        ),
        "android_source": (
            "android/connect-core/src/test/kotlin/com/remedy/groveconnect/core/"
            "NoiseIkTest.kt::snowVectorIkChaChaBlake2s"
        ),
        "android_hex_matched_python": android_match,
        **hs,
        "msg2_payload_hex": _hx(SNOW_MSG2_PAYLOAD),
        "msg2_ciphertext_hex": _hx(msg2_ct),
        "msg3_payload_hex": _hx(SNOW_MSG3_PAYLOAD),
        "msg3_ciphertext_hex": _hx(msg3_ct),
        "android_expected": {
            "msg0_ciphertext_hex": _hx(SNOW_MSG0_CT_ANDROID),
            "msg1_ciphertext_hex": _hx(SNOW_MSG1_CT_ANDROID),
            "msg2_ciphertext_hex": _hx(SNOW_MSG2_CT_ANDROID),
            "msg3_ciphertext_hex": _hx(SNOW_MSG3_CT_ANDROID),
        },
    }


def capture_pair_secret() -> dict[str, Any]:
    """Product PROLOGUE + 32-byte pair secret as first handshake payload."""
    hs = _handshake(
        init_static=REMEDY_PHONE_STATIC,
        init_eph=REMEDY_PHONE_EPH,
        resp_static=REMEDY_HOST_STATIC,
        resp_eph=REMEDY_HOST_EPH,
        prologue=PROLOGUE,
        payload0=REMEDY_PAIR_SECRET,
        payload1=b"",
    )
    for key in ("_send_i", "_recv_i", "_send_r", "_recv_r"):
        hs.pop(key)

    ok_compare = hmac.compare_digest(REMEDY_PAIR_SECRET, REMEDY_PAIR_SECRET)
    wrong_compare = hmac.compare_digest(REMEDY_PAIR_SECRET, REMEDY_WRONG_PS)
    if not ok_compare or wrong_compare:
        raise RuntimeError("pair-secret compare sanity failed")

    return {
        "name": "remedy_pair_secret",
        "notes": (
            "Product prologue remedy-connect/1. Initiator first payload is the "
            "raw 32-byte pair secret (Android HandshakeState / pairSecretRoundtrip). "
            "Python Noise layer returns the decrypted payload; application "
            "constant-time compares (hmac.compare_digest). wrong_ps must fail closed."
        ),
        "android_source": (
            "android/connect-core/src/test/kotlin/com/remedy/groveconnect/core/"
            "NoiseIkTest.kt::pairSecretRoundtripAndWrongPsFailsClosed"
        ),
        "protocol_prologue_utf8": PROLOGUE.decode("utf-8"),
        "pair_secret_hex": _hx(REMEDY_PAIR_SECRET),
        "wrong_pair_secret_hex": _hx(REMEDY_WRONG_PS),
        "pair_secret_verify_ok": ok_compare,
        "wrong_ps_verify_ok": wrong_compare,
        **hs,
    }


def capture_post_split_aead() -> dict[str, Any]:
    """Post-split encrypt/decrypt both directions with product PROLOGUE."""
    hs = _handshake(
        init_static=REMEDY_PHONE_STATIC,
        init_eph=REMEDY_PHONE_EPH,
        resp_static=REMEDY_HOST_STATIC,
        resp_eph=REMEDY_HOST_EPH,
        prologue=PROLOGUE,
        payload0=REMEDY_PAIR_SECRET,
        payload1=b"",
    )
    send_i = hs.pop("_send_i")
    recv_i = hs.pop("_recv_i")
    send_r = hs.pop("_send_r")
    recv_r = hs.pop("_recv_r")

    ct_i = send_i.encrypt_with_ad(b"", REMEDY_MSG2_PLAIN)
    pt_i = recv_r.decrypt_with_ad(b"", ct_i)
    ct_r = send_r.encrypt_with_ad(b"", REMEDY_MSG3_PLAIN)
    pt_r = recv_i.decrypt_with_ad(b"", ct_r)
    if pt_i != REMEDY_MSG2_PLAIN or pt_r != REMEDY_MSG3_PLAIN:
        raise RuntimeError("post-split AEAD roundtrip failed")

    return {
        "name": "remedy_post_split_aead",
        "notes": (
            "Same fixed keys as remedy_pair_secret. After split: initiator→responder "
            "then responder→initiator with empty AD (Noise transport)."
        ),
        "init_static_priv_hex": hs["init_static_priv_hex"],
        "init_eph_priv_hex": hs["init_eph_priv_hex"],
        "resp_static_priv_hex": hs["resp_static_priv_hex"],
        "resp_eph_priv_hex": hs["resp_eph_priv_hex"],
        "prologue_hex": hs["prologue_hex"],
        "handshake_msg0_payload_hex": hs["msg0_payload_hex"],
        "handshake_msg0_ciphertext_hex": hs["msg0_ciphertext_hex"],
        "handshake_msg1_ciphertext_hex": hs["msg1_ciphertext_hex"],
        "transport_i_to_r_plaintext_hex": _hx(REMEDY_MSG2_PLAIN),
        "transport_i_to_r_ciphertext_hex": _hx(ct_i),
        "transport_r_to_i_plaintext_hex": _hx(REMEDY_MSG3_PLAIN),
        "transport_r_to_i_ciphertext_hex": _hx(ct_r),
        "ad_hex": "",
    }


def capture_all() -> dict[str, Path]:
    """Write all Connect Noise_IK fixtures. Returns path map."""
    debug = capture_debug_hash()
    snow = capture_snow_ik()
    pair = capture_pair_secret()
    post = capture_post_split_aead()

    android_matched = bool(
        debug.get("android_hex_matched_python")
        and snow.get("android_hex_matched_python")
    )
    if not android_matched:
        raise RuntimeError(
            "Android hex vectors did not match Python capture — refuse to write"
        )

    meta = {
        "source": "remedy.connect.noise",
        "captured_at": _now(),
        "protocol_name_utf8": PROTOCOL_NAME.decode("utf-8"),
        "protocol_name_hex": _hx(PROTOCOL_NAME),
        "product_prologue_utf8": PROLOGUE.decode("utf-8"),
        "product_prologue_hex": _hx(PROLOGUE),
        "android_hex_matched_python": android_matched,
        "notes": (
            "Exact byte vectors from live Python Noise_IK before Go port. "
            "Snow vectors must stay byte-identical to Android NoiseIkTest. "
            "Regenerate: uv run python -m tests.harness.connect_fixture_capture"
        ),
        "vectors": [debug["name"], snow["name"], pair["name"], post["name"]],
    }

    written = {
        "index": _write("noise_ik_index.json", meta),
        "android_debug_hash": _write("noise_ik_android_debug_hash.json", debug),
        "snow_ik_chacha_blake2s": _write("noise_ik_snow.json", snow),
        "remedy_pair_secret": _write("noise_ik_pair_secret.json", pair),
        "remedy_post_split_aead": _write("noise_ik_post_split.json", post),
        "readme": _write_readme(android_matched=android_matched),
    }
    return written


def _write_readme(*, android_matched: bool) -> Path:
    text = f"""# Connect Noise_IK fixtures (Phase 5)

Exact hex vectors captured from **live Python** ``remedy.connect.noise``
before any Go port. Snow crate vectors mirror Android
``NoiseIkTest.snowVectorIkChaChaBlake2s``.

## Android hex match

Last capture: **{'MATCHED' if android_matched else 'MISMATCH'}**

## Files

| File | Vector name | Meaning |
|------|-------------|---------|
| `noise_ik_android_debug_hash.json` | `android_debug_hash` | Intermediate h0 / prologue / rs / e / k / enc(s) |
| `noise_ik_snow.json` | `snow_ik_chacha_blake2s` | Full IK handshake + post-split AEAD (snow keys) |
| `noise_ik_pair_secret.json` | `remedy_pair_secret` | Product prologue; first payload = 32-byte PS; wrong-ps fail |
| `noise_ik_post_split.json` | `remedy_post_split_aead` | Both-direction transport AEAD after split |
| `noise_ik_index.json` | (meta) | Capture stamp + vector list |

## Regenerating

```text
uv run python -m tests.harness.connect_fixture_capture
uv run pytest tests/test_connect_noise_fixtures.py -q
```

Do not hand-edit hex fields. Re-run capture from Python, then re-verify.
"""
    path = fixture_dir() / "README.md"
    path.write_text(text, encoding="utf-8")
    return path


def main() -> None:
    written = capture_all()
    for name, path in written.items():
        print(f"{name}: {path}")


if __name__ == "__main__":
    main()
