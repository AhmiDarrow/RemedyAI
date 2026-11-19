package com.remedy.groveconnect.core

import java.net.DatagramPacket
import java.net.DatagramSocket
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.net.InetSocketAddress
import java.nio.ByteBuffer
import java.security.MessageDigest

/**
 * Relay-free hole punching through carrier-grade NAT — the phone half of the
 * address swap. Mirrors `native/go/connect/iroh_punch.go`: the topic and the
 * ChaCha20-Poly1305 key derive from the PC's Noise host public key, so the
 * public MQTT brokers only ever see ciphertext.
 */
object PunchCrypto {
    private val AD = "remedy-punch/1".toByteArray(Charsets.US_ASCII)
    private const val NONCE_LEN = 12

    private fun sha256(vararg parts: ByteArray): ByteArray {
        val md = MessageDigest.getInstance("SHA-256")
        for (p in parts) md.update(p)
        return md.digest()
    }

    private fun hex(b: ByteArray): String = b.joinToString("") { "%02x".format(it) }

    /** Topic offers for this PC go to. */
    fun topic(hostPub: ByteArray): String =
        "remedy/punch/" + hex(sha256("remedy-punch|".toByteArray(Charsets.US_ASCII), hostPub)).substring(0, 32)

    /** Topic the PC answers one offer on. */
    fun replyTopic(hostPub: ByteArray, nonce: String): String = topic(hostPub) + "/" + nonce

    private fun key(hostPub: ByteArray): ByteArray =
        sha256("remedy-punch-key|".toByteArray(Charsets.US_ASCII), hostPub)

    /** nonce(12) || ChaCha20-Poly1305(json). */
    fun seal(hostPub: ByteArray, json: String): ByteArray {
        val nonce = Crypto.randomBytes(NONCE_LEN)
        val ct = Crypto.aeadEncryptNonce(key(hostPub), nonce, AD, json.toByteArray(Charsets.UTF_8))
        return nonce + ct
    }

    /** Decrypt a punch message; throws on tampering or the wrong PC. */
    fun open(hostPub: ByteArray, blob: ByteArray): String {
        if (blob.size < NONCE_LEN + Protocol.TAG_LEN || blob.size > 4096) throw NoiseException("punch: bad size")
        val pt = Crypto.aeadDecryptNonce(
            key(hostPub),
            blob.copyOfRange(0, NONCE_LEN),
            AD,
            blob.copyOfRange(NONCE_LEN, blob.size),
        )
        return String(pt, Charsets.UTF_8)
    }
}

/** Minimal STUN (RFC 5389) Binding client: the public address of a UDP socket. */
object Stun {
    private const val MAGIC = 0x2112A442
    val DEFAULT_SERVERS = listOf("stun.l.google.com" to 19302, "stun.cloudflare.com" to 3478)

    fun request(txid: ByteArray): ByteArray {
        require(txid.size == 12)
        return ByteBuffer.allocate(20).putShort(0x0001).putShort(0).putInt(MAGIC).put(txid).array()
    }

    /** Mapped address from a Binding Success, or null if malformed / not ours. */
    fun parse(resp: ByteArray, txid: ByteArray): InetSocketAddress? {
        if (resp.size < 20) return null
        val bb = ByteBuffer.wrap(resp)
        if (bb.getShort(0).toInt() and 0xffff != 0x0101) return null
        val len = bb.getShort(2).toInt() and 0xffff
        if (bb.getInt(4) != MAGIC) return null
        if (!resp.copyOfRange(8, 20).contentEquals(txid)) return null
        if (resp.size < 20 + len) return null
        var off = 20
        var plain: InetSocketAddress? = null
        while (off + 4 <= 20 + len) {
            val type = bb.getShort(off).toInt() and 0xffff
            val alen = bb.getShort(off + 2).toInt() and 0xffff
            off += 4
            if (off + alen > 20 + len) return null
            val v = resp.copyOfRange(off, off + alen)
            off += alen + (4 - alen % 4) % 4
            when (type) {
                0x0020 -> return decode(v, xor = true, txid)
                0x0001 -> plain = decode(v, xor = false, txid)
            }
        }
        return plain
    }

    private fun decode(v: ByteArray, xor: Boolean, txid: ByteArray): InetSocketAddress? {
        if (v.size < 4) return null
        var port = ((v[2].toInt() and 0xff) shl 8) or (v[3].toInt() and 0xff)
        val cookie = ByteBuffer.allocate(4).putInt(MAGIC).array()
        if (xor) port = port xor (MAGIC ushr 16)
        return when (v[1].toInt()) {
            1 -> {
                if (v.size < 8) return null
                val ip = v.copyOfRange(4, 8)
                if (xor) for (i in 0 until 4) ip[i] = (ip[i].toInt() xor cookie[i].toInt()).toByte()
                InetSocketAddress(Inet4Address.getByAddress(ip), port)
            }
            2 -> {
                if (v.size < 20) return null
                val ip = v.copyOfRange(4, 20)
                if (xor) {
                    val k = cookie + txid
                    for (i in 0 until 16) ip[i] = (ip[i].toInt() xor k[i].toInt()).toByte()
                }
                InetSocketAddress(Inet6Address.getByAddress(ip), port)
            }
            else -> null
        }
    }

    /** Ask servers in order which public address they see for [sock]. */
    fun mapped(sock: DatagramSocket, servers: List<Pair<String, Int>> = DEFAULT_SERVERS): InetSocketAddress {
        sock.soTimeout = 1_500
        var last: Exception = NoiseException("no STUN server answered")
        for ((host, port) in servers) {
            val dst = try {
                InetAddress.getAllByName(host).firstOrNull { it is Inet4Address } ?: continue
            } catch (e: Exception) {
                last = e
                continue
            }
            repeat(2) {
                val txid = Crypto.randomBytes(12)
                try {
                    val req = request(txid)
                    sock.send(DatagramPacket(req, req.size, InetSocketAddress(dst, port)))
                    val buf = ByteArray(576)
                    val pkt = DatagramPacket(buf, buf.size)
                    sock.receive(pkt)
                    parse(buf.copyOf(pkt.length), txid)?.let { return it }
                } catch (e: Exception) {
                    last = e
                }
            }
        }
        throw last
    }
}
