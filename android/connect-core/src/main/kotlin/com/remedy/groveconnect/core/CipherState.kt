package com.remedy.groveconnect.core

class CipherState internal constructor(private var key: ByteArray?) {
    var n: Long = 0L
        internal set

    fun hasKey(): Boolean = key != null

    fun encryptWithAd(ad: ByteArray, plaintext: ByteArray): ByteArray {
        val k = key ?: return plaintext.copyOf()
        if (n == -1L) throw NoiseException("nonce exhausted")
        val ct = Crypto.aeadEncrypt(k, n, ad, plaintext)
        n += 1
        return ct
    }

    fun decryptWithAd(ad: ByteArray, ciphertext: ByteArray): ByteArray {
        val k = key ?: return ciphertext.copyOf()
        if (n == -1L) throw NoiseException("nonce exhausted")
        val pt = try {
            Crypto.aeadDecrypt(k, n, ad, ciphertext)
        } catch (e: Exception) {
            throw NoiseException("decrypt failed", e)
        }
        n += 1
        return pt
    }

    /** Encrypt using the explicit 12-byte nonce; n must match the counter. */
    fun encryptRecord(plaintext: ByteArray): Pair<ByteArray, ByteArray> {
        val k = key ?: throw NoiseException("no transport key")
        if (n == -1L) throw NoiseException("nonce exhausted")
        val nonce = Crypto.chachaNonce(n)
        val ct = Crypto.aeadEncryptNonce(k, nonce, ByteArray(0), plaintext)
        n += 1
        return nonce to ct
    }

    fun decryptRecord(nonce: ByteArray, ciphertext: ByteArray): ByteArray {
        val k = key ?: throw NoiseException("no transport key")
        val got = Crypto.nonceToCounter(nonce)
        if (got != n) throw NoiseException("unexpected nonce (replay or reorder)")
        val pt = try {
            Crypto.aeadDecryptNonce(k, nonce, ByteArray(0), ciphertext)
        } catch (e: Exception) {
            throw NoiseException("decrypt failed", e)
        }
        n += 1
        return pt
    }

    /**
     * Noise Rekey for ChaChaPoly: ENCRYPT(k, 2^64-1, zerolen, 32 zero bytes)[:32].
     */
    fun rekey() {
        val k = key ?: return
        val nonce = Crypto.chachaNonce(-1L) // uint64 2^64-1 as 8×0xFF
        val out = Crypto.aeadEncryptNonce(k, nonce, ByteArray(0), ByteArray(32))
        key = out.copyOf(Protocol.KEY_LEN)
        n = 0L
    }

    internal fun setKey(k: ByteArray) {
        key = k.copyOf()
        n = 0L
    }
}

class NoiseException(message: String, cause: Throwable? = null) : Exception(message, cause)
