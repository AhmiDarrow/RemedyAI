package com.remedy.groveconnect.core

import org.bouncycastle.crypto.digests.Blake2sDigest
import org.bouncycastle.crypto.macs.HMac
import org.bouncycastle.crypto.modes.ChaCha20Poly1305
import org.bouncycastle.crypto.params.AEADParameters
import org.bouncycastle.crypto.params.KeyParameter
import org.bouncycastle.crypto.params.X25519PrivateKeyParameters
import org.bouncycastle.crypto.params.X25519PublicKeyParameters
import java.security.SecureRandom

object Crypto {
    private val rng = SecureRandom()

    fun randomBytes(n: Int): ByteArray {
        val out = ByteArray(n)
        rng.nextBytes(out)
        return out
    }

    fun blake2s(vararg parts: ByteArray): ByteArray {
        val d = Blake2sDigest(256)
        for (p in parts) d.update(p, 0, p.size)
        val out = ByteArray(Protocol.HASH_LEN)
        d.doFinal(out, 0)
        return out
    }

    fun hmacBlake2s(key: ByteArray, data: ByteArray): ByteArray {
        val mac = HMac(Blake2sDigest(256))
        mac.init(KeyParameter(key))
        mac.update(data, 0, data.size)
        val out = ByteArray(Protocol.HASH_LEN)
        mac.doFinal(out, 0)
        return out
    }

    /** Noise HKDF: HMAC-BLAKE2s as in the Noise spec. */
    fun hkdf(chainingKey: ByteArray, input: ByteArray, outputs: Int): Array<ByteArray> {
        require(outputs == 2 || outputs == 3)
        val temp = hmacBlake2s(chainingKey, input)
        val o1 = hmacBlake2s(temp, byteArrayOf(0x01))
        val o2 = hmacBlake2s(temp, o1 + byteArrayOf(0x02))
        if (outputs == 2) return arrayOf(o1, o2)
        val o3 = hmacBlake2s(temp, o2 + byteArrayOf(0x03))
        return arrayOf(o1, o2, o3)
    }

    fun x25519Public(privateKey: ByteArray): ByteArray {
        require(privateKey.size == Protocol.KEY_LEN)
        return X25519PrivateKeyParameters(privateKey, 0).generatePublicKey().encoded
    }

    fun x25519Dh(privateKey: ByteArray, publicKey: ByteArray): ByteArray {
        require(privateKey.size == Protocol.KEY_LEN && publicKey.size == Protocol.KEY_LEN)
        val priv = X25519PrivateKeyParameters(privateKey, 0)
        val pub = X25519PublicKeyParameters(publicKey, 0)
        val out = ByteArray(Protocol.KEY_LEN)
        priv.generateSecret(pub, out, 0)
        return out
    }

    fun generateX25519(): Pair<ByteArray, ByteArray> {
        val priv = X25519PrivateKeyParameters(rng)
        return priv.encoded to priv.generatePublicKey().encoded
    }

    /** 12-byte IETF ChaChaPoly nonce: 4 zero bytes || uint64le(n). */
    fun chachaNonce(n: Long): ByteArray {
        val out = ByteArray(Protocol.NONCE_LEN)
        var x = n
        for (i in 0 until 8) {
            out[4 + i] = (x and 0xffL).toByte()
            x = x ushr 8
        }
        return out
    }

    fun nonceToCounter(nonce: ByteArray): Long {
        require(nonce.size == Protocol.NONCE_LEN)
        for (i in 0 until 4) require(nonce[i] == 0.toByte()) { "nonce prefix" }
        var n = 0L
        for (i in 7 downTo 0) {
            n = (n shl 8) or (nonce[4 + i].toInt() and 0xff).toLong()
        }
        return n
    }

    fun aeadEncrypt(key: ByteArray, n: Long, ad: ByteArray, plaintext: ByteArray): ByteArray {
        return aead(encrypt = true, key, chachaNonce(n), ad, plaintext)
    }

    fun aeadDecrypt(key: ByteArray, n: Long, ad: ByteArray, ciphertext: ByteArray): ByteArray {
        return aead(encrypt = false, key, chachaNonce(n), ad, ciphertext)
    }

    fun aeadEncryptNonce(key: ByteArray, nonce: ByteArray, ad: ByteArray, plaintext: ByteArray): ByteArray {
        return aead(encrypt = true, key, nonce, ad, plaintext)
    }

    fun aeadDecryptNonce(key: ByteArray, nonce: ByteArray, ad: ByteArray, ciphertext: ByteArray): ByteArray {
        return aead(encrypt = false, key, nonce, ad, ciphertext)
    }

    private fun aead(
        encrypt: Boolean,
        key: ByteArray,
        nonce: ByteArray,
        ad: ByteArray,
        data: ByteArray,
    ): ByteArray {
        val cipher = ChaCha20Poly1305()
        cipher.init(encrypt, AEADParameters(KeyParameter(key), Protocol.TAG_LEN * 8, nonce, ad))
        val out = ByteArray(cipher.getOutputSize(data.size))
        val n = cipher.processBytes(data, 0, data.size, out, 0)
        val m = cipher.doFinal(out, n)
        return if (n + m == out.size) out else out.copyOf(n + m)
    }

    fun constantTimeEquals(a: ByteArray, b: ByteArray): Boolean {
        if (a.size != b.size) return false
        var r = 0
        for (i in a.indices) r = r or (a[i].toInt() xor b[i].toInt())
        return r == 0
    }
}
