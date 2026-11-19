package com.remedy.groveconnect.core

import java.net.InetSocketAddress
import java.nio.ByteBuffer
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

class PunchTest {
    private val hostPub = ByteArray(32) { 0x42 }

    private fun unhex(s: String) = ByteArray(s.length / 2) { s.substring(it * 2, it * 2 + 2).toInt(16).toByte() }

    // Produced by native/go/connect SealPunch / PunchTopic for hostPub = 0x42 * 32.
    private val goTopic = "remedy/punch/f3bca9b5f57d926906a372bb378a1f1b"
    private val goBlob = "6c8c4f56c3fdec36250a314d5f32cde30bcf4d512fd1697c2293aed01081d50f4d7d5c64c71c95fd" +
        "f329a15b58bed118a9bf36ebbd76dcce57d1321a8cf38fbceb0b0d74edab900957170cf581f3a1f029af7d341a410b34975934d4"

    @Test
    fun matchesGoTopicAndOpensGoBlob() {
        assertEquals(goTopic, PunchCrypto.topic(hostPub))
        assertEquals("$goTopic/abcd", PunchCrypto.replyTopic(hostPub, "abcd"))
        val json = PunchCrypto.open(hostPub, unhex(goBlob))
        assertTrue(json.contains("203.0.113.7:40000"), json)
        assertTrue(json.contains("0123456789abcdef"), json)
    }

    @Test
    fun sealRoundTripAndRejectsWrongHostOrTamper() {
        val blob = PunchCrypto.seal(hostPub, "{\"v\":1}")
        assertEquals("{\"v\":1}", PunchCrypto.open(hostPub, blob))
        assertFailsWith<Exception> { PunchCrypto.open(ByteArray(32) { 1 }, blob) }
        blob[blob.size - 1] = (blob[blob.size - 1].toInt() xor 1).toByte()
        assertFailsWith<Exception> { PunchCrypto.open(hostPub, blob) }
    }

    @Test
    fun stunRequestAndXorMappedParse() {
        val txid = ByteArray(12) { 3 }
        val req = Stun.request(txid)
        assertEquals(20, req.size)
        assertEquals(0x2112A442, ByteBuffer.wrap(req).getInt(4))
        val cookie = ByteBuffer.allocate(4).putInt(0x2112A442).array()
        val ip = byteArrayOf(74, 167.toByte(), 84, 108)
        val xport = 7403 xor (0x2112A442 ushr 16)
        val attr = ByteBuffer.allocate(12).putShort(0x0020).putShort(8)
            .put(0).put(1).putShort(xport.toShort())
            .put(ByteArray(4) { (ip[it].toInt() xor cookie[it].toInt()).toByte() }).array()
        val resp = ByteBuffer.allocate(32).putShort(0x0101).putShort(12).putInt(0x2112A442).put(txid).put(attr).array()
        assertEquals(InetSocketAddress("74.167.84.108", 7403), Stun.parse(resp, txid))
        assertNull(Stun.parse(resp, ByteArray(12)))
        assertNull(Stun.parse(resp.copyOf(10), txid))
    }
}
