package com.remedy.groveconnect.core

import java.io.IOException
import java.io.InputStream
import java.net.ServerSocket
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue
import kotlin.test.assertFailsWith
import kotlin.concurrent.thread

class MqttRendezvousTest {

    @Test
    fun varintRoundtrip() {
        for (n in listOf(0, 1, 127, 128, 16383, 16384, 2097151)) {
            val enc = MqttCodec.encodeVarint(n)
            val (value, consumed) = MqttCodec.decodeVarint(enc, 0)
            assertEquals(n, value)
            assertEquals(enc.size, consumed)
        }
    }

    @Test
    fun connackParse() {
        assertEquals(0, MqttCodec.connackCode(byteArrayOf(0x20, 0x02, 0x00, 0x00)))
        assertEquals(5, MqttCodec.connackCode(byteArrayOf(0x20, 0x02, 0x00, 0x05)))
        assertNull(MqttCodec.connackCode(byteArrayOf(0x10, 0x00)))
        assertNull(MqttCodec.connackCode(byteArrayOf(0x20, 0x01, 0x00)))
    }

    @Test
    fun buildConnectShape() {
        val pkt = MqttCodec.buildConnect("abc123")
        assertEquals(0x10, pkt[0].toInt() and 0xFF)
        val hay = String(pkt, Charsets.US_ASCII)
        assertTrue(hay.contains("MQTT"), hay)
    }

    @Test
    fun publishRoundtripLargePayload() {
        val topic = "remedy/" + "ab".repeat(16) + "/pc"
        val payload = ByteArray(300) { it.toByte() } // >127 exercises multi-byte varint
        val pkt = MqttCodec.buildPublish(9, topic, payload, 1)
        val msg = MqttCodec.parsePublish(pkt)
        assertNotNull(msg)
        assertEquals(topic, msg!!.topic)
        assertTrue(msg.payload.contentEquals(payload))
        assertEquals(1, msg.qos)
        assertEquals(9, msg.packetId)
    }

    @Test
    fun publishParseQos0() {
        val pkt = MqttCodec.buildPublish(0, "t", byteArrayOf(1, 2), 0)
        val msg = MqttCodec.parsePublish(pkt)
        assertNotNull(msg)
        assertEquals(0, msg!!.qos)
        assertEquals(0, msg.packetId)
        assertTrue(msg.payload.contentEquals(byteArrayOf(1, 2)))
    }

    @Test
    fun pubackShape() {
        assertTrue(MqttCodec.buildPuback(0x1234).contentEquals(byteArrayOf(0x40, 0x02, 0x12, 0x34)))
    }

    @Test
    fun subackGrantedChecks() {
        assertTrue(MqttCodec.subackGranted(byteArrayOf(0x90.toByte(), 0x03, 0x00, 0x07, 0x01)))
        assertFalse(MqttCodec.subackGranted(byteArrayOf(0x90.toByte(), 0x03, 0x00, 0x07, 0x80.toByte())))
        assertFalse(MqttCodec.subackGranted(byteArrayOf(0x00, 0x00)))
        assertFalse(MqttCodec.subackGranted(byteArrayOf(0x90.toByte(), 0x02, 0x00, 0x07)))
    }

    @Test
    fun queueInputServesBytesAcrossRecords() {
        val q = QueueInputStream()
        q.enqueue(byteArrayOf(1, 2, 3))
        q.enqueue(byteArrayOf(4, 5))
        val out = ByteArray(5)
        var off = 0
        while (off < 5) {
            val n = q.read(out, off, 5 - off)
            assertTrue(n > 0)
            off += n
        }
        assertTrue(out.contentEquals(byteArrayOf(1, 2, 3, 4, 5)))
        q.close()
        assertEquals(-1, q.read())
    }

    @Test
    fun queueCloseWakesBlockedReader() {
        val q = QueueInputStream()
        var result = 99
        val reader = thread { result = q.read() }
        Thread.sleep(50)
        q.close()
        reader.join(1_000)
        assertFalse(reader.isAlive)
        assertEquals(-1, result)
    }

    @Test
    fun packetIdParsingRejectsTruncatedAck() {
        assertEquals(0x1234, MqttClient.packetId(byteArrayOf(0x40, 0x02, 0x12, 0x34)))
        assertNull(MqttClient.packetId(byteArrayOf(0x40, 0x02, 0x12)))
    }

    @Test
    fun brokerDisconnectCannotMasqueradeAsPublishAck() {
        ServerSocket(0).use { server ->
            val broker = thread(isDaemon = true) {
                server.accept().use { socket ->
                    readPacket(socket.getInputStream()) // CONNECT
                    socket.getOutputStream().write(byteArrayOf(0x20, 0x02, 0x00, 0x00))
                    socket.getOutputStream().flush()
                    readPacket(socket.getInputStream()) // PUBLISH, then disconnect without PUBACK
                }
            }
            val client = MqttClient("127.0.0.1", server.localPort, timeoutMs = 1_000)
            try {
                client.connect()
                assertFailsWith<IOException> {
                    client.publish("remedy/test/pc", byteArrayOf(1, 2, 3))
                }
            } finally {
                client.close()
                broker.join(1_000)
            }
        }
    }

    @Test
    fun framePumpParsesSplitFrames() {
        val frames = ArrayList<ByteArray>()
        val pump = FramePumpOutputStream { payload -> frames += payload }
        val p1 = "hello".toByteArray()
        val p2 = ByteArray(300) { 0x42 }
        val f1 = u32be(p1.size) + p1
        val f2 = u32be(p2.size) + p2
        pump.write(f1, 0, f1.size)
        pump.write(f2, 0, 7) // split frame 2 across two writes
        assertEquals(1, frames.size)
        pump.write(f2, 7, f2.size - 7)
        assertEquals(2, frames.size)
        assertTrue(frames[0].contentEquals(p1))
        assertTrue(frames[1].contentEquals(p2))
    }

    @Test
    fun sidHexShape() {
        val sid = ByteArray(16) { it.toByte() }
        val hex = MqttClient.sidHex(sid)
        assertEquals(32, hex.length)
        assertTrue(hex.all { it in '0'..'9' || it in 'a'..'f' })
    }

    private fun u32be(n: Int): ByteArray = byteArrayOf(
        (n ushr 24).toByte(),
        (n ushr 16).toByte(),
        (n ushr 8).toByte(),
        n.toByte(),
    )

    private fun readPacket(input: InputStream): ByteArray {
        val output = ArrayList<Byte>()
        output += input.read().also { if (it < 0) throw IOException("closed") }.toByte()
        var remaining = 0
        var multiplier = 1
        while (true) {
            val next = input.read()
            if (next < 0) throw IOException("closed")
            output += next.toByte()
            remaining += (next and 0x7f) * multiplier
            if (next and 0x80 == 0) break
            multiplier *= 128
        }
        repeat(remaining) {
            val next = input.read()
            if (next < 0) throw IOException("closed")
            output += next.toByte()
        }
        return output.toByteArray()
    }
}
