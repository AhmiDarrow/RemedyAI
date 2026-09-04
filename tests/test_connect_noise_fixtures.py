"""Re-verify Connect Noise_IK fixtures against live Python (no drift).

Fixtures are captured by ``tests.harness.connect_fixture_capture`` from
``remedy.connect.noise``. This module only loads JSON+hex and asserts
byte-identical handshake / AEAD output — it does not regenerate files.
"""

from __future__ import annotations

import hmac
import json
from pathlib import Path

from remedy.connect.noise import (
    PROLOGUE,
    PROTOCOL_NAME,
    HandshakeState,
    KeyPair,
    _aead_encrypt,
    _dh,
    _hash,
    _hkdf,
)

FIXTURE_DIR = Path(__file__).resolve().parent / "fixtures" / "connect"

REQUIRED = (
    "noise_ik_index.json",
    "noise_ik_android_debug_hash.json",
    "noise_ik_snow.json",
    "noise_ik_pair_secret.json",
    "noise_ik_post_split.json",
    "record_framing.json",
)


def _load(name: str) -> dict:
    path = FIXTURE_DIR / name
    assert path.is_file(), f"missing fixture {path}; run connect_fixture_capture"
    return json.loads(path.read_text(encoding="utf-8"))


def _b(hex_s: str) -> bytes:
    return bytes.fromhex(hex_s)


def test_fixture_files_present() -> None:
    for name in REQUIRED:
        path = FIXTURE_DIR / name
        assert path.is_file(), name
        assert path.stat().st_size > 40, name


def test_index_records_android_match() -> None:
    idx = _load("noise_ik_index.json")
    assert idx["protocol_name_utf8"] == PROTOCOL_NAME.decode("utf-8")
    assert idx["product_prologue_utf8"] == PROLOGUE.decode("utf-8")
    assert idx["android_hex_matched_python"] is True
    assert "snow_ik_chacha_blake2s" in idx["vectors"]
    assert "remedy_pair_secret" in idx["vectors"]
    assert "remedy_post_split_aead" in idx["vectors"]
    assert "remedy_record_framing" in idx["vectors"]


def test_android_debug_hash_matches_live_python() -> None:
    fx = _load("noise_ik_android_debug_hash.json")
    assert fx["android_hex_matched_python"] is True

    h0 = _hash(PROTOCOL_NAME)
    assert h0 == _b(fx["h0_hex"])

    prologue = _b(
        "5468657265206973206e6f20726967687420616e642077726f6e672e20546865"
        "72652773206f6e6c792066756e20616e6420626f72696e672e"
    )
    h1 = _hash(h0 + prologue)
    assert h1 == _b(fx["h_prologue_hex"])

    init_rs = _b(
        "ea82fd2e81d1285f1b2029e46ca7bcaeeeafed15396d002bd434624a4d580655"
    )
    h2 = _hash(h1 + init_rs)
    assert h2 == _b(fx["h_rs_hex"])

    init_eph = _b(
        "cc95b4ccc4912c5a52c8d2f6b808e13712392c4468f4e3f02a7d2d1590cb9178"
    )
    e_pub = KeyPair.from_private(init_eph).public
    h3 = _hash(h2 + e_pub)
    assert h3 == _b(fx["h_e_hex"])

    dh = _dh(KeyPair.from_private(init_eph), init_rs)
    _ck, k = _hkdf(h0, dh, 2)
    assert k == _b(fx["k_after_es_hex"])

    init_static = _b(
        "b7e117ce8ede06ceb89500799a3778d097fc54a3f90bea744493dfc24ec21f32"
    )
    enc_s = _aead_encrypt(k, 0, h3, KeyPair.from_private(init_static).public)
    assert enc_s == _b(fx["enc_s_hex"])


def test_snow_ik_fixture_matches_live_python_and_android() -> None:
    fx = _load("noise_ik_snow.json")
    assert fx["android_hex_matched_python"] is True
    assert fx["name"] == "snow_ik_chacha_blake2s"

    phone = KeyPair.from_private(_b(fx["init_static_priv_hex"]))
    host = KeyPair.from_private(_b(fx["resp_static_priv_hex"]))
    assert host.public == _b(fx["resp_static_pub_hex"])
    assert phone.public == _b(fx["init_static_pub_hex"])

    init = HandshakeState(
        initiator=True,
        s=phone,
        rs=host.public,
        prologue=_b(fx["prologue_hex"]),
    )
    resp = HandshakeState(
        initiator=False,
        s=host,
        prologue=_b(fx["prologue_hex"]),
    )
    init.set_ephemeral_for_test(_b(fx["init_eph_priv_hex"]))
    resp.set_ephemeral_for_test(_b(fx["resp_eph_priv_hex"]))

    msg0 = init.write_message(_b(fx["msg0_payload_hex"]))
    assert msg0 == _b(fx["msg0_ciphertext_hex"])
    assert msg0 == _b(fx["android_expected"]["msg0_ciphertext_hex"])
    assert resp.read_message(msg0) == _b(fx["msg0_payload_hex"])

    msg1 = resp.write_message(_b(fx["msg1_payload_hex"]))
    assert msg1 == _b(fx["msg1_ciphertext_hex"])
    assert msg1 == _b(fx["android_expected"]["msg1_ciphertext_hex"])
    assert init.read_message(msg1) == _b(fx["msg1_payload_hex"])

    send_i, recv_i = init.split()
    send_r, recv_r = resp.split()

    msg2 = send_i.encrypt_with_ad(b"", _b(fx["msg2_payload_hex"]))
    assert msg2 == _b(fx["msg2_ciphertext_hex"])
    assert msg2 == _b(fx["android_expected"]["msg2_ciphertext_hex"])
    assert recv_r.decrypt_with_ad(b"", msg2) == _b(fx["msg2_payload_hex"])

    msg3 = send_r.encrypt_with_ad(b"", _b(fx["msg3_payload_hex"]))
    assert msg3 == _b(fx["msg3_ciphertext_hex"])
    assert msg3 == _b(fx["android_expected"]["msg3_ciphertext_hex"])
    assert recv_i.decrypt_with_ad(b"", msg3) == _b(fx["msg3_payload_hex"])


def test_pair_secret_first_payload_success() -> None:
    fx = _load("noise_ik_pair_secret.json")
    assert fx["name"] == "remedy_pair_secret"
    assert _b(fx["prologue_hex"]) == PROLOGUE
    ps = _b(fx["pair_secret_hex"])
    assert len(ps) == 32
    assert fx["pair_secret_verify_ok"] is True
    assert fx["wrong_ps_verify_ok"] is False

    phone = KeyPair.from_private(_b(fx["init_static_priv_hex"]))
    host = KeyPair.from_private(_b(fx["resp_static_priv_hex"]))
    init = HandshakeState(initiator=True, s=phone, rs=host.public, prologue=PROLOGUE)
    resp = HandshakeState(initiator=False, s=host, prologue=PROLOGUE)
    init.set_ephemeral_for_test(_b(fx["init_eph_priv_hex"]))
    resp.set_ephemeral_for_test(_b(fx["resp_eph_priv_hex"]))

    msg0 = init.write_message(ps)
    assert msg0 == _b(fx["msg0_ciphertext_hex"])
    got = resp.read_message(msg0)
    assert got == ps
    assert hmac.compare_digest(got, ps)

    msg1 = resp.write_message(b"")
    assert msg1 == _b(fx["msg1_ciphertext_hex"])
    assert init.read_message(msg1) == b""


def test_wrong_ps_fails_closed_after_decrypt() -> None:
    """Decrypt succeeds; constant-time compare against wrong PS fails (Android)."""
    fx = _load("noise_ik_pair_secret.json")
    ps = _b(fx["pair_secret_hex"])
    wrong = _b(fx["wrong_pair_secret_hex"])
    assert ps != wrong

    phone = KeyPair.from_private(_b(fx["init_static_priv_hex"]))
    host = KeyPair.from_private(_b(fx["resp_static_priv_hex"]))
    init = HandshakeState(initiator=True, s=phone, rs=host.public, prologue=PROLOGUE)
    resp = HandshakeState(initiator=False, s=host, prologue=PROLOGUE)
    init.set_ephemeral_for_test(_b(fx["init_eph_priv_hex"]))
    resp.set_ephemeral_for_test(_b(fx["resp_eph_priv_hex"]))

    msg0 = init.write_message(ps)
    got = resp.read_message(msg0)
    assert got == ps
    # Application layer (Android HandshakeState.verifyPairSecret): fail closed.
    assert len(got) == 32
    assert not hmac.compare_digest(got, wrong)
    # Same ciphertext must not authenticate a different expected PS.
    assert hmac.compare_digest(got, ps)


def test_post_split_encrypt_decrypt_both_directions() -> None:
    fx = _load("noise_ik_post_split.json")
    assert fx["name"] == "remedy_post_split_aead"

    phone = KeyPair.from_private(_b(fx["init_static_priv_hex"]))
    host = KeyPair.from_private(_b(fx["resp_static_priv_hex"]))
    init = HandshakeState(initiator=True, s=phone, rs=host.public, prologue=PROLOGUE)
    resp = HandshakeState(initiator=False, s=host, prologue=PROLOGUE)
    init.set_ephemeral_for_test(_b(fx["init_eph_priv_hex"]))
    resp.set_ephemeral_for_test(_b(fx["resp_eph_priv_hex"]))

    msg0 = init.write_message(_b(fx["handshake_msg0_payload_hex"]))
    assert msg0 == _b(fx["handshake_msg0_ciphertext_hex"])
    resp.read_message(msg0)
    msg1 = resp.write_message(b"")
    assert msg1 == _b(fx["handshake_msg1_ciphertext_hex"])
    init.read_message(msg1)

    send_i, recv_i = init.split()
    send_r, recv_r = resp.split()
    ad = _b(fx["ad_hex"]) if fx["ad_hex"] else b""

    ct_i = send_i.encrypt_with_ad(ad, _b(fx["transport_i_to_r_plaintext_hex"]))
    assert ct_i == _b(fx["transport_i_to_r_ciphertext_hex"])
    assert recv_r.decrypt_with_ad(ad, ct_i) == _b(fx["transport_i_to_r_plaintext_hex"])

    ct_r = send_r.encrypt_with_ad(ad, _b(fx["transport_r_to_i_plaintext_hex"]))
    assert ct_r == _b(fx["transport_r_to_i_ciphertext_hex"])
    assert recv_i.decrypt_with_ad(ad, ct_r) == _b(fx["transport_r_to_i_plaintext_hex"])
