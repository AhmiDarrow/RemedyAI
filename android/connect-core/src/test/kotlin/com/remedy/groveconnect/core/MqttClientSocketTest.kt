package com.remedy.groveconnect.core

import java.io.InputStream
import java.net.ServerSocket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import kotlin.test.Test
import kotlin.test.assertTrue

/**
 * Regression: MqttClient over a REAL socket against a scripted fake broker.
 *
 * Exercises connect/subscribe/publish/onMessage through the actual
 * readHeader/readPacket socket path — the path that used to drop the MQTT
 * remaining-length varint bytes and throw "truncated packet", which broke the
 * mobile-data rendezvous while the LAN path (Wi-Fi) kept working. The codec
 * unit tests could not catch this because they fed complete packets directly.
 */
class MqttClientSocketTest {

    private class FakeBroker {
        private val server = ServerSocket(0)
        val localPort: Int get() = server.localPort
        val done = CountDownLatch(1)
        var error: Throwable? = null
        private val thread = Thread { run() }

        fun start(): FakeBroker {
            thread.isDaemon = true
            thread.start()
            return this
        }

        private fun run() {
            try {
                server.use { srv ->
                    val sock = srv.accept()
                    sock.use { s ->
                        val inp = s.getInputStream()
                        val out = s.getOutputStream()
                        // 1. CONNECT -> CONNACK accepted
                        readPacket(inp)
                        out.write(byteArrayOf(0x20, 0x02, 0x00, 0x00)); out.flush()
                        // 2. SUBSCRIBE -> parse packet id + first topic, grant it
                        val sub = readPacket(inp)
                        val subBody = sub.second
                        val subPid = ((subBody[0].toInt() and 0xFF) shl 8) or (subBody[1].toInt() and 0xFF)
                        val tlen = ((subBody[2].toInt() and 0xFF) shl 8) or (subBody[3].toInt() and 0xFF)
                        val topic = String(subBody, 4, tlen, Charsets.UTF_8)
                        out.write(byteArrayOf(0x90.toByte(), 0x03, (subPid ushr 8).toByte(), subPid.toByte(), 0x00))
                        out.flush()
                        // 3. inbound PUBLISH (QoS1) on the subscribed topic
                        val payload = "hello-from-broker".toByteArray(Charsets.UTF_8)
                        val body = ByteArray(2 + topic.length + 2 + payload.size)
                        body[0] = (topic.length ushr 8).toByte(); body[1] = topic.length.toByte()
                        System.arraycopy(topic.toByteArray(Charsets.UTF_8), 0, body, 2, topic.length)
                        val pubPid = 0x0102
                        body[2 + topic.length] = (pubPid ushr 8).toByte()
                        body[3 + topic.length] = pubPid.toByte()
                        System.arraycopy(payload, 0, body, 4 + topic.length, payload.size)
                        out.write(byteArrayOf(0x32) + encodeVarint(body.size) + body)
                        out.flush()
                        // 4. consume client PUBACK + its PUBLISH; PUBACK the publish
                        while (true) {
                            val pkt = readPacket(inp)
                            when (pkt.first.toInt() and 0xF0) {
                                0x40 -> Unit // client's PUBACK for our inbound message
                                0x30 -> {
                                    val b = pkt.second
                                    val tl = ((b[0].toInt() and 0xFF) shl 8) or (b[1].toInt() and 0xFF)
                                    val pid = ((b[2 + tl].toInt() and 0xFF) shl 8) or (b[2 + tl + 1].toInt() and 0xFF)
                                    out.write(byteArrayOf(0x40, 0x02, (pid ushr 8).toByte(), pid.toByte()))
                                    out.flush()
                                    return
                                }
                                else -> Unit // PINGREQ etc.
                            }
                        }
                    }
                }
            } catch (t: Throwable) {
                error = t
            } finally {
                done.countDown()
            }
        }

        private fun readPacket(inp: InputStream): Pair<Byte, ByteArray> {
            val first = readExact(inp, 1)[0]
            var value = 0
            var multiplier = 1
            val varint = java.io.ByteArrayOutputStream(4)
            while (true) {
                val b = readExact(inp, 1)[0].toInt() and 0xFF
                varint.write(b)
                value += (b and 0x7F) * multiplier
                if (b and 0x80 == 0) break
                multiplier *= 128
            }
            val body = if (value > 0) readExact(inp, value) else ByteArray(0)
            return first to body
        }

        private fun readExact(inp: InputStream, n: Int): ByteArray {
            val out = ByteArray(n)
            var off = 0
            while (off < n) {
                val got = inp.read(out, off, n - off)
                if (got < 0) throw java.io.IOException("stream closed")
                off += got
            }
            return out
        }

        private fun encodeVarint(n: Int): ByteArray {
            val out = java.io.ByteArrayOutputStream(4)
            var v = n
            while (true) {
                var b = v % 128
                v /= 128
                if (v > 0) b = b or 0x80
                out.write(b)
                if (v == 0) return out.toByteArray()
            }
        }
    }

    @Test
    fun clientConnectSubscribePublishOverRealSocket() {
        val broker = FakeBroker().start()
        val gotMessage = CountDownLatch(1)
        var received: ByteArray? = null

        val client = MqttClient(
            "127.0.0.1",
            broker.localPort,
            timeoutMs = 5_000,
            clientId = "socket-test",
        )
        client.onMessage = { _, payload ->
            received = payload
            gotMessage.countDown()
        }
        try {
            // Would throw "truncated packet" before the varint-assembly fix.
            client.connect()
            client.subscribe(listOf("remedy/0123456789abcdef0123456789abcdef/phone"), 1)
            assertTrue(
                gotMessage.await(5, TimeUnit.SECONDS),
                "inbound PUBLISH never delivered",
            )
            assertTrue(received!!.contentEquals("hello-from-broker".toByteArray(Charsets.UTF_8)))
            client.publish("remedy/0123456789abcdef0123456789abcdef/pc", byteArrayOf(1, 2, 3, 4))
        } finally {
            client.close()
        }
        assertTrue(broker.done.await(5, TimeUnit.SECONDS))
        broker.error?.let { throw it }
    }
}
