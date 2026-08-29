package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertContentEquals
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertTrue

class RecordCodecTest {
    @Test
    fun transportRecordRoundtrip() {
        val k = ByteArray(32) { 3 }
        val send = CipherState(k.copyOf())
        val recv = CipherState(k.copyOf())
        val rec = RecordCodec.encodeTransport(send, "abc".toByteArray())
        assertEquals(4 + 12 + 3 + 16, rec.size)
        val len = RecordCodec.readU32be(rec)
        assertEquals(12 + 3 + 16, len)
        val pt = RecordCodec.decodeTransport(recv, rec.copyOfRange(4, rec.size))
        assertContentEquals("abc".toByteArray(), pt)
        assertEquals(1, send.n)
        assertEquals(1, recv.n)
    }

    @Test
    fun replayNonceFailsClosed() {
        val k = ByteArray(32) { 4 }
        val send = CipherState(k.copyOf())
        val recv = CipherState(k.copyOf())
        val rec = RecordCodec.encodeTransport(send, byteArrayOf(1))
        val body = rec.copyOfRange(4, rec.size)
        RecordCodec.decodeTransport(recv, body)
        assertFailsWith<NoiseException> { RecordCodec.decodeTransport(recv, body) }
    }

    @Test
    fun httpFrameRoundtrip() {
        val req = HttpFrame.HttpRequest("GET", "/api/sessions?limit=20", "Accept: application/json\r\n", ByteArray(0))
        val encoded = HttpFrame.encodeRequest(req)
        val frames = HttpFrame.fragment(HttpFrame.TYPE_HTTP_REQ, 7, encoded)
        assertEquals(1, frames.size)
        val hdr = HttpFrame.decode(frames[0])
        assertTrue(hdr.fin)
        assertEquals(7, hdr.id)
        val back = HttpFrame.decodeRequest(hdr.payload)
        assertEquals("GET", back.method)
        assertEquals("/api/sessions?limit=20", back.target)
    }

    @Test
    fun oversizedPlaintextRejected() {
        val k = ByteArray(32) { 5 }
        val send = CipherState(k)
        assertFailsWith<IllegalArgumentException> {
            RecordCodec.encodeTransport(send, ByteArray(Protocol.MAX_PLAINTEXT + 1))
        }
    }
}
