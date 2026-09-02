package com.remedy.groveconnect.core

/**
 * Grove Connect wire protocol (phone initiator). Must match the Python host.
 *
 * Noise: `Noise_IK_25519_ChaChaPoly_BLAKE2s`
 * Prologue: UTF-8 `remedy-connect/1` (exactly that string; pair secret is not
 * mixed into the prologue).
 * IK: phone knows host static from QR `hp=`. First handshake payload is the
 * 32-byte pair secret (`ps=`). Host decrypts and constant-time compares;
 * mismatch fails closed. Second payload is empty.
 *
 * Handshake TCP framing (before Split):
 *   `u32be length | handshake_bytes`
 *
 * Transport records (after Split):
 *   `u32be length | 12-byte nonce | ciphertext`
 *   length = 12 + ciphertext (tag included in ciphertext).
 *   nonce = 4 zero bytes || uint64le(n)  (Noise ChaChaPoly nonce).
 *   The host (record.py MAX_RECORD) refuses any record whose body
 *   (nonce || ciphertext) exceeds 65536 bytes, so the largest plaintext a
 *   record may carry is 65536 - 12 - 16 = 65508 bytes.
 *
 * Rekey: 2^16 records or 15 minutes. Sender emits an inner REKEY frame, then
 * Rekey()s the send cipher. Receiver Rekey()s the receive cipher after
 * decrypting REKEY.
 *
 * Pairing QR is urlsafe-base64 (Python `base64.urlsafe_b64encode`, padding
 * optional on decode). Never put `local_api_token` on the phone or in QR.
 */
object Protocol {
    const val NAME = "Noise_IK_25519_ChaChaPoly_BLAKE2s"
    const val PROLOGUE = "remedy-connect/1"
    const val QR_HEADER = "remedy-connect/1"
    const val DEFAULT_PORT = 7401
    const val TAG_LEN = 16
    const val NONCE_LEN = 12
    const val KEY_LEN = 32
    const val HASH_LEN = 32
    const val REKEY_AFTER_RECORDS = 65536L
    const val REKEY_AFTER_MS = 15L * 60L * 1000L

    /** Hard cap on `nonce || ciphertext`; mirrors the host's MAX_RECORD. */
    const val MAX_RECORD_BODY = 65536

    /** Largest plaintext one record can carry without the host dropping the session. */
    const val MAX_PLAINTEXT = MAX_RECORD_BODY - NONCE_LEN - TAG_LEN
}
