package com.remedy.groveconnect.core

import org.bouncycastle.crypto.digests.Blake2sDigest
import java.security.MessageDigest

/** 16-byte rendezvous tokens. The relay sees these, never the pair secret in the clear. */
object SessionId {
    private val PAIR = "remedy-connect/1|pair|".toByteArray(Charsets.UTF_8)
    private val DEV = "remedy-connect/1|dev|".toByteArray(Charsets.UTF_8)
    private val RDV = "remedy-connect/1|rdv|".toByteArray(Charsets.UTF_8)
    private val PIPE = byteArrayOf('|'.code.toByte())

    /**
     * Public MQTT brokers accept wildcard subscribers, so a rendezvous id that
     * never changes lets anyone watching a broker enumerate live machines and
     * keep returning to the same topic. Public-broker ids therefore carry a
     * coarse time bucket and go stale on their own. The relay id (see [device])
     * is unchanged: a relay is a host the owner chose.
     */
    const val RDV_BUCKET_SECONDS = 3600L

    /** Rotation bucket for a wall-clock time in seconds since the epoch. */
    fun rdvBucket(epochSeconds: Long): Long = epochSeconds / RDV_BUCKET_SECONDS

    /** Current rotation bucket. */
    fun rdvBucketNow(): Long = rdvBucket(System.currentTimeMillis() / 1000L)

    /** Rotating public-broker rendezvous id for a paired device. */
    fun rendezvous(hostPub: ByteArray, devicePub: ByteArray, bucket: Long): ByteArray {
        require(hostPub.size == Protocol.KEY_LEN && devicePub.size == Protocol.KEY_LEN)
        return blake2s16(RDV, hostPub, PIPE, devicePub, PIPE, bucket.toString().toByteArray(Charsets.UTF_8))
    }

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
