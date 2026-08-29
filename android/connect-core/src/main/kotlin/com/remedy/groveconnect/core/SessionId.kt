package com.remedy.groveconnect.core

import org.bouncycastle.crypto.digests.Blake2sDigest
import java.security.MessageDigest

/** 16-byte rendezvous tokens. The relay sees these, never the pair secret in the clear. */
object SessionId {
    private val PAIR = "remedy-connect/1|pair|".toByteArray(Charsets.UTF_8)
    private val DEV = "remedy-connect/1|dev|".toByteArray(Charsets.UTF_8)

    fun pair(hostPub: ByteArray, pairSecret: ByteArray): ByteArray {
        require(hostPub.size == Protocol.KEY_LEN && pairSecret.size == Protocol.KEY_LEN)
        return blake2s16(PAIR, hostPub, byteArrayOf('|'.code.toByte()), pairSecret)
    }

    fun device(hostPub: ByteArray, devicePub: ByteArray): ByteArray {
        require(hostPub.size == Protocol.KEY_LEN && devicePub.size == Protocol.KEY_LEN)
        return blake2s16(DEV, hostPub, byteArrayOf('|'.code.toByte()), devicePub)
    }

    fun deviceIdHex(devicePub: ByteArray): String {
        val md = MessageDigest.getInstance("SHA-256")
        return md.digest(devicePub).joinToString("") { "%02x".format(it) }.take(32)
    }

    private fun blake2s16(vararg parts: ByteArray): ByteArray {
        val d = Blake2sDigest(128)
        for (p in parts) d.update(p, 0, p.size)
        val out = ByteArray(16)
        d.doFinal(out, 0)
        return out
    }
}
