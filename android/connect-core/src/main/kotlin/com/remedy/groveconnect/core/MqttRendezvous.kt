package com.remedy.groveconnect.core

import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.atomic.AtomicInteger

/**
 * Minimal MQTT 3.1.1 (QoS1) rendezvous for Grove Connect on mobile data.
 *
 * Both the PC and the phone dial *out* to a public MQTT broker (no account,
 * no binary, no VPS) and meet on a topic named by the 16-byte session id:
 *
 * ```
 * remedy/<session-id-hex>/pc
 * remedy/<session-id-hex>/phone
 * ```
 *
 * The broker only ever sees the random session id and Noise ciphertext — the
 * same trust model as an owner relay. One Noise record travels per MQTT
 * message (MQTT supplies the framing; the u32be prefix is stripped/re-added).
 */
object MqttCodec {
    const val MAX_RECORD = 65536
    private const val CONNECT = 0x10
    private const val CONNACK = 0x20
    private const val PUBLISH = 0x30
    private const val PUBACK = 0x40
    private const val SUBSCRIBE = 0x80
    private const val SUBACK = 0x90
    private const val PINGREQ = 0xC0

    val PINGREQ_BYTES = byteArrayOf(PINGREQ.toByte(), 0x00)

    fun encodeVarint(value: Int): ByteArray {
        require(value in 0..268435455) { "varint out of range" }
        val out = java.io.ByteArrayOutputStream(4)
        var v = value
        while (true) {
            var byte = v % 128
            v /= 128
            if (v > 0) byte = byte or 0x80
            out.write(byte)
            if (v == 0) return out.toByteArray()
        }
    }

    /** Return (value, bytesConsumed). */
    fun decodeVarint(data: ByteArray, offset: Int): Pair<Int, Int> {
        var value = 0
        var multiplier = 1
        var i = offset
        while (true) {
            if (i >= data.size) throw IOException("truncated varint")
            val b = data[i].toInt() and 0xFF
            value += (b and 0x7F) * multiplier
            if (b and 0x80 == 0) return value to (i - offset + 1)
            if (multiplier > 128 * 128 * 128) throw IOException("varint too long")
            multiplier *= 128
            i++
        }
    }

    private fun utf8Field(text: String): ByteArray {
        val raw = text.toByteArray(Charsets.UTF_8)
        require(raw.size <= 65535) { "MQTT string too long" }
        return byteArrayOf(
            (raw.size ushr 8).toByte(),
            (raw.size and 0xFF).toByte(),
        ) + raw
    }

    fun buildConnect(clientId: String, keepalive: Int = 30): ByteArray {
        val body = byteArrayOf(
            0x00, 0x04, 'M'.code.toByte(), 'Q'.code.toByte(), 'T'.code.toByte(), 'T'.code.toByte(),
            0x04, 0x02, (keepalive ushr 8).toByte(), (keepalive and 0xFF).toByte(),
        ) + utf8Field(clientId)
        return byteArrayOf(CONNECT.toByte()) + encodeVarint(body.size) + body
    }

    /** Return the CONNACK return code, or null if the packet is malformed. */
    fun connackCode(packet: ByteArray): Int? {
        if (packet.size < 4 || packet[0].toInt() and 0xFF != CONNACK) return null
        return try {
            val (remaining, consumed) = decodeVarint(packet, 1)
            if (remaining < 2 || 1 + consumed + 2 > packet.size) null
            else packet[2 + consumed].toInt() and 0xFF
        } catch (_: IOException) {
            null
        }
    }

    fun buildSubscribe(packetId: Int, topics: List<String>, qos: Int = 1): ByteArray {
        var body = byteArrayOf((packetId ushr 8).toByte(), (packetId and 0xFF).toByte())
        for (topic in topics) {
            body += utf8Field(topic) + byteArrayOf((qos and 0x03).toByte())
        }
        return byteArrayOf((SUBSCRIBE or 0x02).toByte()) + encodeVarint(body.size) + body
    }

    /** True when the SUBACK grants every requested topic (no 0x80). */
    fun subackGranted(packet: ByteArray): Boolean {
        if (packet.size < 5 || packet[0].toInt() and 0xFF != SUBACK) return false
        return try {
            val (remaining, consumed) = decodeVarint(packet, 1)
            val body = packet.copyOfRange(1 + consumed, packet.size)
            body.size == remaining && remaining >= 3 && body.drop(2).none { it.toInt() and 0xFF == 0x80 }
        } catch (_: IOException) {
            false
        }
    }

    fun buildPublish(packetId: Int, topic: String, payload: ByteArray, qos: Int = 1): ByteArray {
        require(payload.size <= MAX_RECORD) { "publish payload too large" }
        var body = utf8Field(topic)
        if (qos > 0) body += byteArrayOf((packetId ushr 8).toByte(), (packetId and 0xFF).toByte())
        body += payload
        val flags = if (qos == 1) 0x02 else 0x00
        return byteArrayOf((PUBLISH or flags).toByte()) + encodeVarint(body.size) + body
    }

    class MqttPublish(val topic: String, val payload: ByteArray, val qos: Int, val packetId: Int)

    fun parsePublish(packet: ByteArray): MqttPublish? {
        if (packet.size < 2) return null
        val kind = packet[0].toInt() and 0xF0
        if (kind != PUBLISH) return null
        return try {
            val (remaining, consumed) = decodeVarint(packet, 1)
            val body = packet.copyOfRange(1 + consumed, packet.size)
            if (body.size != remaining) return null
            val qos = (packet[0].toInt() shr 1) and 0x03
            if (qos == 3) return null
            val tlen = ((body[0].toInt() and 0xFF) shl 8) or (body[1].toInt() and 0xFF)
            if (2 + tlen > body.size) return null
            val topic = String(body, 2, tlen, Charsets.UTF_8)
            var offset = 2 + tlen
            var packetId = 0
            if (qos > 0) {
                if (offset + 2 > body.size) return null
                packetId = ((body[offset].toInt() and 0xFF) shl 8) or (body[offset + 1].toInt() and 0xFF)
                offset += 2
            }
            MqttPublish(topic, body.copyOfRange(offset, body.size), qos, packetId)
        } catch (_: Exception) {
            null
        }
    }

    fun buildPuback(packetId: Int): ByteArray =
        byteArrayOf(PUBACK.toByte(), 0x02, (packetId ushr 8).toByte(), (packetId and 0xFF).toByte())
}

/** InputStream that serves bytes from queued arrays (one Noise record each). */
class QueueInputStream : InputStream() {
    private val queue = ArrayBlockingQueue<ByteArray>(MAX_QUEUED_RECORDS)
    private val closeSentinel = ByteArray(0)
    @Volatile
    private var closed = false
    private var head: ByteArray = ByteArray(0)
    private var pos = 0

    fun enqueue(data: ByteArray): Boolean {
        if (data.isEmpty() || closed) return false
        return queue.offer(data)
    }

    override fun read(): Int {
        val b = ByteArray(1)
        return if (read(b, 0, 1) > 0) b[0].toInt() and 0xFF else -1
    }

    override fun read(b: ByteArray, off: Int, len: Int): Int {
        if (len == 0) return 0
        while (pos >= head.size) {
            if (closed) return -1
            val next = queue.take()
            if (next === closeSentinel || closed) return -1
            head = next
            pos = 0
        }
        val n = minOf(len, head.size - pos)
        System.arraycopy(head, pos, b, off, n)
        pos += n
        return n
    }

    override fun close() {
        if (closed) return
        closed = true
        queue.clear()
        queue.offer(closeSentinel)
    }

    private companion object {
        const val MAX_QUEUED_RECORDS = 64
    }
}

/**
 * OutputStream that parses u32be-framed Noise records and hands each payload
 * to [onFrame] (which publishes it over MQTT). Buffers partial frames.
 */
class FramePumpOutputStream(private val onFrame: (ByteArray) -> Unit) : OutputStream() {
    private val lock = Object()
    private val buf = java.io.ByteArrayOutputStream()
    @Volatile
    private var closed = false

    override fun write(b: Int) {
        synchronized(lock) {
            if (closed) throw IOException("frame pump closed")
            buf.write(b)
            pumpLocked()
        }
    }

    override fun write(b: ByteArray, off: Int, len: Int) {
        synchronized(lock) {
            if (closed) throw IOException("frame pump closed")
            buf.write(b, off, len)
            pumpLocked()
        }
    }

    private fun pumpLocked() {
        val data = buf.toByteArray()
        var off = 0
        while (off + 4 <= data.size) {
            val len = ((data[off].toInt() and 0xFF) shl 24) or
                ((data[off + 1].toInt() and 0xFF) shl 16) or
                ((data[off + 2].toInt() and 0xFF) shl 8) or
                (data[off + 3].toInt() and 0xFF)
            if (len < 0 || len > MqttCodec.MAX_RECORD) throw IOException("frame too large")
            if (off + 4 + len > data.size) break
            val payload = data.copyOfRange(off + 4, off + 4 + len)
            onFrame(payload)
            off += 4 + len
        }
        if (off > 0) {
            val rest = data.copyOfRange(off, data.size)
            buf.reset()
            buf.write(rest)
        }
    }

    override fun close() {
        closed = true
    }
}

/**
 * Minimal MQTT 3.1.1 client: connect/subscribe/publish (QoS1) with a reader
 * thread that dispatches inbound PUBLISH to [onMessage].
 */
class MqttClient(
    private val host: String,
    private val port: Int,
    private val timeoutMs: Int = 6_000,
    private val clientId: String = randomClientId(),
) {
    @Volatile
    var onMessage: ((topic: String, payload: ByteArray) -> Unit)? = null

    private val pktIds = AtomicInteger(1)
    private val pubWait = Object()
    private val writeLock = Object()
    private var pubPending = 0
    private val subWait = Object()
    private var subPendingId = 0
    private var subPendingCount = 0
    private var subGranted = false

    private var sock: Socket? = null
    private var input: InputStream? = null
    private var output: OutputStream? = null
    private var readerThread: Thread? = null
    private var keepaliveThread: Thread? = null
    @Volatile
    private var closed = false
    @Volatile
    private var terminalError: IOException? = null

    fun connect() {
        val s = Socket()
        s.tcpNoDelay = true
        try {
            s.connect(InetSocketAddress(host, port), timeoutMs)
            s.soTimeout = timeoutMs
            sock = s
            input = s.getInputStream()
            output = s.getOutputStream()
            writePacket(MqttCodec.buildConnect(clientId, 30))
            val resp = readPacket(input!!, 4)
            val code = MqttCodec.connackCode(resp)
                ?: throw IOException("bad MQTT CONNACK")
            if (code != 0) {
                close()
                throw IOException("MQTT broker refused ($code)")
            }
            s.soTimeout = 0
            startReader()
            startKeepalive()
        } catch (e: Exception) {
            try {
                s.close()
            } catch (_: Exception) {
            }
            throw e
        }
    }

    fun subscribe(topics: List<String>, qos: Int = 1) {
        val id = nextPacketId()
        synchronized(subWait) {
            subPendingId = id
            subPendingCount = topics.size
            subGranted = false
        }
        writePacket(MqttCodec.buildSubscribe(id, topics, qos))
        synchronized(subWait) {
            val deadline = System.currentTimeMillis() + 5_000
            while (subPendingCount > 0 && System.currentTimeMillis() < deadline) {
                (subWait as Object).wait(200)
            }
            if (subPendingCount > 0) throw IOException("SUBACK timeout")
            terminalError?.let { throw it }
            if (!subGranted) throw IOException("MQTT broker refused subscription")
        }
    }

    fun publish(topic: String, payload: ByteArray) {
        val id = nextPacketId()
        synchronized(pubWait) {
            pubPending = id
            writePacket(MqttCodec.buildPublish(id, topic, payload, 1))
            val deadline = System.currentTimeMillis() + 20_000
            while (pubPending != 0 && System.currentTimeMillis() < deadline) {
                (pubWait as Object).wait(200)
            }
            terminalError?.let { throw it }
            if (pubPending != 0) throw IOException("PUBACK timeout")
        }
    }

    private fun startReader() {
        val inp = input ?: return
        val t = Thread({
            try {
                while (!closed) {
                    val (first, varint, remaining) = readHeader(inp)
                    val body = if (remaining > 0) readExact(inp, remaining) else ByteArray(0)
                    val packet = first + varint + body
                    val kind = packet[0].toInt() and 0xFF
                    when (kind and 0xF0) {
                        0x40 -> { // PUBACK
                            synchronized(pubWait) {
                                val ackId = packetId(packet)
                                if (ackId != null && ackId == pubPending) {
                                    pubPending = 0
                                    (pubWait as Object).notifyAll()
                                }
                            }
                        }
                        0x90 -> { // SUBACK
                            synchronized(subWait) {
                                val ackId = packetId(packet)
                                if (ackId != null && ackId == subPendingId) {
                                    subPendingCount = 0
                                    subGranted = MqttCodec.subackGranted(packet)
                                    (subWait as Object).notifyAll()
                                }
                            }
                        }
                        0x30 -> { // PUBLISH
                            val msg = MqttCodec.parsePublish(packet)
                            if (msg != null) {
                                if (msg.qos > 0) {
                                    if (!closed) writePacket(MqttCodec.buildPuback(msg.packetId))
                                }
                                val h = onMessage
                                if (h != null && msg.topic.isNotEmpty()) {
                                    try {
                                        h(msg.topic, msg.payload)
                                    } catch (_: Exception) {
                                    }
                                }
                            }
                        }
                        else -> Unit // PINGRESP / others: ignore
                    }
                }
            } catch (e: Exception) {
                close(IOException("MQTT connection lost", e))
            }
        }, "grove-rdv-mqtt")
        t.isDaemon = true
        t.start()
        readerThread = t
    }

    private fun startKeepalive() {
        val t = Thread({
            try {
                while (!closed) {
                    Thread.sleep(12_000)
                    if (closed) break
                    if (!closed) writePacket(MqttCodec.PINGREQ_BYTES)
                }
            } catch (e: Exception) {
                close(IOException("MQTT keepalive failed", e))
            }
        }, "grove-rdv-keepalive")
        t.isDaemon = true
        t.start()
        keepaliveThread = t
    }

    /** Reads one MQTT fixed header: returns (fixed header byte, varint bytes, remaining length). */
    private fun readHeader(inp: InputStream): Triple<ByteArray, ByteArray, Int> {
        val first = readExact(inp, 1)
        val varint = java.io.ByteArrayOutputStream(4)
        var value = 0
        var multiplier = 1
        while (true) {
            val b = readExact(inp, 1)[0].toInt() and 0xFF
            varint.write(b)
            value += (b and 0x7F) * multiplier
            if (value > MAX_PACKET_REMAINING) throw IOException("MQTT packet too large")
            if (b and 0x80 == 0) break
            if (multiplier > 128 * 128 * 128) throw IOException("varint too long")
            multiplier *= 128
        }
        return Triple(first, varint.toByteArray(), value)
    }

    private fun readPacket(inp: InputStream, expectMin: Int): ByteArray {
        val (first, varint, remaining) = readHeader(inp)
        val body = if (remaining > 0) readExact(inp, remaining) else ByteArray(0)
        val packet = first + varint + body
        if (packet.size < expectMin) throw IOException("truncated packet")
        return packet
    }

    private fun readExact(inp: InputStream, n: Int): ByteArray {
        if (n < 0 || n > MAX_PACKET_REMAINING) throw IOException("MQTT packet too large")
        val out = ByteArray(n)
        var off = 0
        while (off < n) {
            val got = inp.read(out, off, n - off)
            if (got < 0) throw IOException("MQTT stream closed")
            off += got
        }
        return out
    }

    fun close(error: IOException? = IOException("MQTT connection closed")) {
        if (terminalError == null && error != null) terminalError = error
        closed = true
        try {
            sock?.close()
        } catch (_: Exception) {
        }
        readerThread?.interrupt()
        keepaliveThread?.interrupt()
        synchronized(pubWait) {
            pubPending = 0
            (pubWait as Object).notifyAll()
        }
        synchronized(subWait) {
            subPendingCount = 0
            (subWait as Object).notifyAll()
        }
    }

    companion object {
        private const val MAX_PACKET_REMAINING = MqttCodec.MAX_RECORD + 1024

        internal fun packetId(packet: ByteArray): Int? {
            if (packet.size < 4) return null
            return try {
                val (_, consumed) = MqttCodec.decodeVarint(packet, 1)
                val at = 1 + consumed
                if (at + 2 > packet.size) null
                else ((packet[at].toInt() and 0xff) shl 8) or (packet[at + 1].toInt() and 0xff)
            } catch (_: Exception) {
                null
            }
        }
        fun randomClientId(): String {
            val bytes = ByteArray(16)
            java.security.SecureRandom().nextBytes(bytes)
            val sb = StringBuilder(32)
            for (b in bytes) sb.append("%02x".format(b))
            return sb.toString()
        }

        fun sidHex(sid: ByteArray): String {
            require(sid.size == 16) { "session id must be 16 bytes" }
            val sb = StringBuilder(32)
            for (b in sid) sb.append("%02x".format(b))
            return sb.toString()
        }
    }

    private fun nextPacketId(): Int {
        while (true) {
            val current = pktIds.getAndUpdate { if (it >= 65535 || it <= 0) 1 else it + 1 }
            if (current in 1..65535) return current
        }
    }

    private fun writePacket(packet: ByteArray) {
        synchronized(writeLock) {
            if (closed) throw IOException("MQTT connection closed")
            output?.write(packet) ?: throw IOException("MQTT connection not open")
            output?.flush()
        }
    }
}

/**
 * Rendezvous bridge: MQTT topics <-> Noise record streams. One direction only
 * per side: the PC publishes to ``pc`` and subscribes to ``phone``; the phone
 * does the mirror. `input`/`output` are handed to the Noise handshake exactly
 * like a TCP socket's streams.
 */
class RendezvousStreams(
    private val client: MqttClient,
    sid: ByteArray,
    private val role: String,
) {
    private val outTopic: String
    private val inTopic: String
    private val queueInput = QueueInputStream()
    private val pumpOutput: FramePumpOutputStream

    val input: InputStream get() = queueInput
    val output: OutputStream get() = pumpOutput

    init {
        require(role == "pc" || role == "phone") { "role must be pc or phone" }
        val hex = MqttClient.sidHex(sid)
        outTopic = "remedy/$hex/$role"
        inTopic = "remedy/$hex/${if (role == "pc") "phone" else "pc"}"
        pumpOutput = FramePumpOutputStream { payload ->
            client.publish(outTopic, payload)
        }
    }

    fun start() {
        client.subscribe(listOf(inTopic), 1)
        client.onMessage = { topic, payload ->
            if (topic == inTopic && payload.size <= MqttCodec.MAX_RECORD) {
                val len = payload.size
                val framed = ByteArray(4 + len)
                framed[0] = (len ushr 24).toByte()
                framed[1] = (len ushr 16).toByte()
                framed[2] = (len ushr 8).toByte()
                framed[3] = len.toByte()
                System.arraycopy(payload, 0, framed, 4, len)
                if (!queueInput.enqueue(framed)) {
                    // A peer or broker flooding faster than Noise can consume is unsafe.
                    close()
                }
            }
        }
    }

    fun close() {
        queueInput.close()
        client.close()
    }
}
