package com.remedy.groveconnect.core

import java.io.InputStream
import java.io.OutputStream
import java.nio.ByteBuffer
import java.nio.ByteOrder

/**
 * Transport record: `u32be length | 12-byte nonce | ciphertext`.
 * Handshake frame: `u32be length | bytes` (no nonce).
 */
object RecordCodec {
    fun writeU32be(n: Int): ByteArray {
        require(n >= 0)
        return byteArrayOf(
            ((n ushr 24) and 0xff).toByte(),
            ((n ushr 16) and 0xff).toByte(),
            ((n ushr 8) and 0xff).toByte(),
            (n and 0xff).toByte(),
        )
    }

    fun readU32be(b: ByteArray, off: Int = 0): Int {
        require(off + 4 <= b.size)
        return ((b[off].toInt() and 0xff) shl 24) or
            ((b[off + 1].toInt() and 0xff) shl 16) or
            ((b[off + 2].toInt() and 0xff) shl 8) or
            (b[off + 3].toInt() and 0xff)
    }

    fun encodeHandshake(message: ByteArray): ByteArray {
        require(message.size <= Protocol.MAX_PLAINTEXT) { "handshake too large" }
        return writeU32be(message.size) + message
    }

    fun encodeTransport(send: CipherState, plaintext: ByteArray): ByteArray {
        require(plaintext.size <= Protocol.MAX_PLAINTEXT) { "record plaintext exceeds 64 KiB" }
        val (nonce, ct) = send.encryptRecord(plaintext)
        val body = nonce + ct
        require(body.size <= Protocol.MAX_RECORD_BODY)
        return writeU32be(body.size) + body
    }

    fun decodeTransport(recv: CipherState, recordBody: ByteArray): ByteArray {
        if (recordBody.size < Protocol.NONCE_LEN + Protocol.TAG_LEN) {
            throw NoiseException("short transport record")
        }
        val nonce = recordBody.copyOfRange(0, Protocol.NONCE_LEN)
        val ct = recordBody.copyOfRange(Protocol.NONCE_LEN, recordBody.size)
        return recv.decryptRecord(nonce, ct)
    }

    fun writeFully(out: OutputStream, data: ByteArray) {
        out.write(data)
        out.flush()
    }

    fun readExact(input: InputStream, n: Int): ByteArray {
        val buf = ByteArray(n)
        var off = 0
        while (off < n) {
            val r = input.read(buf, off, n - off)
            if (r < 0) throw NoiseException("connection closed")
            off += r
        }
        return buf
    }

    fun readLengthPrefixed(input: InputStream, max: Int = Protocol.MAX_RECORD_BODY): ByteArray {
        val len = readU32be(readExact(input, 4))
        if (len < 0 || len > max) throw NoiseException("record length $len out of range")
        return readExact(input, len)
    }

    fun putU16(buf: ByteBuffer, v: Int) {
        buf.order(ByteOrder.BIG_ENDIAN).putShort(v.toShort())
    }
}
