package com.remedy.groveconnect.core

import java.nio.ByteBuffer
import java.nio.ByteOrder
import java.util.concurrent.atomic.AtomicInteger

/**
 * Inner multiplexed HTTP over Noise records.
 *
 * ```
 * u8 version=1 | u8 type | u32be id | u8 flags | u32be len | payload
 * ```
 *
 * flags bit0 = FIN (last fragment of this message).
 */
object HttpFrame {
    const val VERSION: Byte = 1
    const val TYPE_HTTP_REQ: Byte = 0x01
    const val TYPE_HTTP_RES: Byte = 0x02
    const val TYPE_PING: Byte = 0x10
    const val TYPE_PONG: Byte = 0x11
    const val TYPE_REKEY: Byte = 0x20
    const val FLAG_FIN: Int = 0x01

    private val ids = AtomicInteger(1)

    fun nextId(): Int {
        var v: Int
        do {
            v = ids.getAndIncrement()
            if (v <= 0) ids.compareAndSet(v + 1, 1)
        } while (v <= 0)
        return v
    }

    data class Header(
        val type: Byte,
        val id: Int,
        val flags: Int,
        val payload: ByteArray,
    ) {
        val fin: Boolean get() = (flags and FLAG_FIN) != 0
    }

    fun encode(type: Byte, id: Int, payload: ByteArray, fin: Boolean = true): ByteArray {
        val buf = ByteBuffer.allocate(1 + 1 + 4 + 1 + 4 + payload.size).order(ByteOrder.BIG_ENDIAN)
        buf.put(VERSION)
        buf.put(type)
        buf.putInt(id)
        buf.put((if (fin) FLAG_FIN else 0).toByte())
        buf.putInt(payload.size)
        buf.put(payload)
        return buf.array()
    }

    fun decode(raw: ByteArray): Header {
        if (raw.size < 11) throw NoiseException("short inner frame")
        val buf = ByteBuffer.wrap(raw).order(ByteOrder.BIG_ENDIAN)
        val ver = buf.get()
        if (ver != VERSION) throw NoiseException("inner version $ver")
        val type = buf.get()
        val id = buf.int
        val flags = buf.get().toInt() and 0xff
        val len = buf.int
        if (len < 0 || len > Protocol.MAX_PLAINTEXT) throw NoiseException("inner payload too large")
        if (buf.remaining() < len) throw NoiseException("short inner payload")
        val payload = ByteArray(len)
        buf.get(payload)
        return Header(type, id, flags, payload)
    }

    data class HttpRequest(
        val method: String,
        val target: String,
        val headers: String,
        val body: ByteArray,
    )

    data class HttpResponse(
        val status: Int,
        val headers: String,
        val body: ByteArray,
    )

    fun encodeRequest(req: HttpRequest): ByteArray {
        val method = req.method.toByteArray(Charsets.US_ASCII)
        val target = req.target.toByteArray(Charsets.UTF_8)
        val headers = req.headers.toByteArray(Charsets.UTF_8)
        val buf = ByteBuffer.allocate(
            1 + method.size + 2 + target.size + 2 + headers.size + 4 + req.body.size,
        ).order(ByteOrder.BIG_ENDIAN)
        require(method.size <= 255)
        buf.put(method.size.toByte())
        buf.put(method)
        buf.putShort(target.size.toShort())
        buf.put(target)
        buf.putShort(headers.size.toShort())
        buf.put(headers)
        buf.putInt(req.body.size)
        buf.put(req.body)
        return buf.array()
    }

    fun decodeRequest(payload: ByteArray): HttpRequest {
        val buf = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
        val mlen = buf.get().toInt() and 0xff
        val method = ByteArray(mlen).also { buf.get(it) }.toString(Charsets.US_ASCII)
        val tlen = buf.short.toInt() and 0xffff
        val target = ByteArray(tlen).also { buf.get(it) }.toString(Charsets.UTF_8)
        val hlen = buf.short.toInt() and 0xffff
        val headers = ByteArray(hlen).also { buf.get(it) }.toString(Charsets.UTF_8)
        val blen = buf.int
        if (blen < 0 || buf.remaining() < blen) throw NoiseException("short http body")
        val body = ByteArray(blen).also { buf.get(it) }
        return HttpRequest(method, target, headers, body)
    }

    fun encodeResponse(res: HttpResponse): ByteArray {
        val headers = res.headers.toByteArray(Charsets.UTF_8)
        val buf = ByteBuffer.allocate(2 + 2 + headers.size + 4 + res.body.size).order(ByteOrder.BIG_ENDIAN)
        buf.putShort(res.status.toShort())
        buf.putShort(headers.size.toShort())
        buf.put(headers)
        buf.putInt(res.body.size)
        buf.put(res.body)
        return buf.array()
    }

    fun decodeResponse(payload: ByteArray): HttpResponse {
        if (looksLikeHttp1(payload)) return decodeHttp1(payload)
        val buf = ByteBuffer.wrap(payload).order(ByteOrder.BIG_ENDIAN)
        val status = buf.short.toInt() and 0xffff
        val hlen = buf.short.toInt() and 0xffff
        val headers = ByteArray(hlen).also { buf.get(it) }.toString(Charsets.UTF_8)
        val blen = buf.int
        if (blen < 0 || buf.remaining() < blen) throw NoiseException("short http body")
        val body = ByteArray(blen).also { buf.get(it) }
        return HttpResponse(status, headers, body)
    }

    fun looksLikeHttp1(payload: ByteArray): Boolean {
        if (payload.size < 5) return false
        return payload[0] == 'H'.code.toByte() &&
            payload[1] == 'T'.code.toByte() &&
            payload[2] == 'T'.code.toByte() &&
            payload[3] == 'P'.code.toByte() &&
            payload[4] == '/'.code.toByte()
    }

    fun decodeHttp1(payload: ByteArray): HttpResponse {
        val sep = indexOf(payload, CRLF2) ?: throw NoiseException("short http res")
        val head = payload.copyOfRange(0, sep).toString(Charsets.ISO_8859_1)
        val rest = payload.copyOfRange(sep + 4, payload.size)
        val lines = head.split("\r\n")
        val status = lines.firstOrNull()?.split(" ")?.getOrNull(1)?.toIntOrNull() ?: 502
        val headersList = lines.drop(1).filter { it.isNotBlank() }
        val chunked = headersList.any {
            it.startsWith("Transfer-Encoding:", ignoreCase = true) &&
                it.contains("chunked", ignoreCase = true)
        }
        val keep = headersList.filterNot {
            it.startsWith("Transfer-Encoding:", ignoreCase = true) ||
                it.startsWith("Content-Length:", ignoreCase = true)
        }
        val body = if (chunked) decodeChunked(rest) else rest
        val hdr = keep.joinToString("\r\n").let { if (it.isEmpty()) it else "$it\r\n" }
        return HttpResponse(status, hdr, body)
    }

    private val CRLF2 = byteArrayOf(13, 10, 13, 10)

    private fun indexOf(hay: ByteArray, needle: ByteArray): Int? {
        if (needle.isEmpty() || hay.size < needle.size) return null
        outer@ for (i in 0..hay.size - needle.size) {
            for (j in needle.indices) {
                if (hay[i + j] != needle[j]) continue@outer
            }
            return i
        }
        return null
    }

    private fun decodeChunked(data: ByteArray): ByteArray {
        val out = java.io.ByteArrayOutputStream(data.size)
        var pos = 0
        while (pos < data.size) {
            val nl = indexOfFrom(data, pos, 13, 10) ?: break
            val sizeLine = data.copyOfRange(pos, nl).toString(Charsets.US_ASCII)
            val n = sizeLine.substringBefore(';').trim().toIntOrNull(16) ?: break
            pos = nl + 2
            if (n <= 0) break
            val end = (pos + n).coerceAtMost(data.size)
            out.write(data, pos, end - pos)
            pos = end
            if (pos + 1 < data.size && data[pos] == 13.toByte() && data[pos + 1] == 10.toByte()) {
                pos += 2
            }
        }
        return out.toByteArray()
    }

    private fun indexOfFrom(hay: ByteArray, start: Int, a: Byte, b: Byte): Int? {
        var i = start
        while (i + 1 < hay.size) {
            if (hay[i] == a && hay[i + 1] == b) return i
            i++
        }
        return null
    }

    const val HEADER_LEN = 11

    /**
     * Split a large inner frame into Noise-record-sized pieces. Every returned
     * frame (header + payload) fits one transport record, so the encrypted
     * record body never exceeds [Protocol.MAX_RECORD_BODY].
     */
    fun fragment(type: Byte, id: Int, payload: ByteArray, maxPlain: Int = Protocol.MAX_PLAINTEXT): List<ByteArray> {
        val chunk = (maxPlain - HEADER_LEN).coerceAtLeast(1)
        if (payload.size <= chunk) {
            return listOf(encode(type, id, payload, fin = true))
        }
        val out = ArrayList<ByteArray>()
        var off = 0
        while (off < payload.size) {
            val end = minOf(payload.size, off + chunk)
            val fin = end == payload.size
            out += encode(type, id, payload.copyOfRange(off, end), fin)
            off = end
        }
        return out
    }
}
