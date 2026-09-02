package com.remedy.groveconnect.connect

import com.remedy.groveconnect.core.CipherState
import com.remedy.groveconnect.core.DenyPath
import com.remedy.groveconnect.core.HandshakeState
import com.remedy.groveconnect.core.HttpFrame
import com.remedy.groveconnect.core.MqttClient
import com.remedy.groveconnect.core.NoiseException
import com.remedy.groveconnect.core.Protocol
import com.remedy.groveconnect.core.QrPayload
import com.remedy.groveconnect.core.RecordCodec
import com.remedy.groveconnect.core.RendezvousStreams
import com.remedy.groveconnect.core.SessionId
import java.io.InputStream
import java.io.OutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.util.concurrent.ConcurrentHashMap
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit

class ConnectClient(
    private val localStaticPriv: ByteArray,
    private val localStaticPub: ByteArray,
) {
    @Volatile
    var socket: Socket? = null
        private set

    @Volatile
    private var send: CipherState? = null

    @Volatile
    private var recv: CipherState? = null

    private var input: InputStream? = null
    private var output: OutputStream? = null
    private val sendLock = Any()
    private val pending = ConcurrentHashMap<Int, Pending>()
    private val fragments = ConcurrentHashMap<Int, ArrayList<ByteArray>>()
    @Volatile
    private var reader: Thread? = null
    @Volatile
    var closed = false
        private set
    private var lastRekeyAt = System.currentTimeMillis()

    /** How this session reached the PC: lan, v6, relay, or rdv. */
    @Volatile
    var via: String = "lan"
        private set

    private var rdv: RendezvousStreams? = null

    /** True on relay / rendezvous transports, where junk records are tolerated. */
    @Volatile
    private var lenientRecords = false

    /**
     * First contact after scanning a QR. [deviceName] (e.g. the phone model)
     * is sent inside the encrypted handshake payload so the PC lists this
     * device by name instead of the generic "phone".
     */
    fun connect(qr: QrPayload, timeoutMs: Int = 8_000, preferRelay: Boolean = false, deviceName: String? = null) {
        val errors = ArrayList<Exception>()
        val lanMs = 1_500
        val relayHost = qr.relayHost
        val relayPort = qr.relayPort
        val rdvHosts = qr.rdvHosts
        val tsHost = qr.tailscaleHost
        val tsPort = qr.tailscalePort
        val payload = pairPayload(qr.pairSecret, deviceName)
        // Tailscale first: the tailnet IP works on Wi-Fi AND mobile data
        // (DERP relays handle NAT), so it is the universal path when present.
        if (tsHost != null && tsPort != null) {
            try {
                connectTcp(tsHost, tsPort, timeoutMs, qr.hostPub, qr.pairSecret, hello = payload)
                via = "tailscale"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        if (preferRelay) {
            // Mobile data: the LAN is unreachable by definition — go straight
            // to the owner relay (if any) then the public rendezvous.
            if (relayHost != null && relayPort != null) {
                try {
                    val sid = SessionId.pair(qr.hostPub, qr.pairSecret)
                    connectTcp(relayHost, relayPort, timeoutMs, qr.hostPub, qr.pairSecret, hello = payload, sessionId = sid)
                    via = "relay"
                    return
                } catch (e: Exception) {
                    errors += e
                }
            }
            if (rdvHosts.isNotEmpty()) {
                try {
                    val sid = SessionId.pair(qr.hostPub, qr.pairSecret)
                    connectRendezvous(rdvHosts, sid, qr.hostPub, qr.pairSecret, hello = payload, timeoutMs = timeoutMs)
                    via = "rdv"
                    return
                } catch (e: Exception) {
                    errors += e
                }
            }
            throw errors.lastOrNull() ?: NoiseException("Could not reach the PC")
        }
        try {
            connectTcp(qr.lanHost, qr.lanPort, lanMs, qr.hostPub, qr.pairSecret, hello = payload)
            via = "lan"
            return
        } catch (e: Exception) {
            errors += e
        }
        val v6 = qr.v6
        if (!v6.isNullOrBlank()) {
            try {
                val (h, p) = parseHostPort(v6, qr.lanPort)
                connectTcp(h, p, lanMs, qr.hostPub, qr.pairSecret, hello = payload)
                via = "v6"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        if (relayHost != null && relayPort != null) {
            try {
                val sid = SessionId.pair(qr.hostPub, qr.pairSecret)
                connectTcp(relayHost, relayPort, timeoutMs, qr.hostPub, qr.pairSecret, hello = payload, sessionId = sid)
                via = "relay"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        if (rdvHosts.isNotEmpty()) {
            try {
                val sid = SessionId.pair(qr.hostPub, qr.pairSecret)
                connectRendezvous(rdvHosts, sid, qr.hostPub, qr.pairSecret, hello = payload, timeoutMs = timeoutMs)
                via = "rdv"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        throw errors.lastOrNull() ?: NoiseException("Could not reach the PC")
    }

    /**
     * Handshake payload for an unpaired phone. With no name the raw 32-byte
     * secret is sent (the host accepts that shape); with a name the host's
     * `pair\0<secret>\0<name>` form is used so the device gets a real label.
     */
    private fun pairPayload(secret: ByteArray, deviceName: String?): ByteArray? {
        var label = deviceName
            ?.replace("\u0000", "")
            ?.trim()
            ?.takeIf { it.isNotEmpty() }
            ?: return null
        // The host caps the label at 80 bytes; cut on a character boundary.
        while (label.isNotEmpty() && label.toByteArray(Charsets.UTF_8).size > 80) {
            label = label.dropLast(1)
        }
        val head = "pair".toByteArray(Charsets.UTF_8) + byteArrayOf(0)
        return head + secret + byteArrayOf(0) + label.toByteArray(Charsets.UTF_8)
    }

    fun reconnect(
        hostPub: ByteArray,
        lanHost: String,
        lanPort: Int,
        tailscaleHost: String?,
        tailscalePort: Int?,
        relayHost: String?,
        relayPort: Int?,
        rdvHosts: List<Pair<String, Int>>,
        devicePub: ByteArray,
        timeoutMs: Int = 8_000,
        preferRelay: Boolean = false,
    ) {
        val hello = ("hello\u0000" + SessionId.deviceIdHex(devicePub)).toByteArray(Charsets.UTF_8)
        val errors = ArrayList<Exception>()
        // Tailscale first: universal path (Wi-Fi + mobile data). This is the
        // PC's own listener, not a relay splice: no session-id preamble (the
        // host would read those 16 bytes as a bogus record length and drop us).
        if (tailscaleHost != null && tailscalePort != null) {
            try {
                connectTcp(tailscaleHost, tailscalePort, timeoutMs, hostPub, pairSecret = null, hello = hello)
                via = "tailscale"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        if (preferRelay && (relayHost != null || rdvHosts.isNotEmpty())) {
            // Mobile data: skip the doomed LAN probe, dial the relay first.
            if (relayHost != null && relayPort != null) {
                try {
                    val sid = SessionId.device(hostPub, devicePub)
                    connectTcp(relayHost, relayPort, timeoutMs, hostPub, pairSecret = null, hello = hello, sessionId = sid)
                    via = "relay"
                    return
                } catch (e: Exception) {
                    errors += e
                }
            }
            if (rdvHosts.isNotEmpty()) {
                try {
                    val sid = SessionId.device(hostPub, devicePub)
                    connectRendezvous(rdvHosts, sid, hostPub, pairSecret = null, hello = hello, timeoutMs = timeoutMs)
                    via = "rdv"
                    return
                } catch (e: Exception) {
                    errors += e
                }
            }
            throw errors.lastOrNull() ?: NoiseException("Could not reach the PC")
        }
        try {
            connectTcp(lanHost, lanPort, 1_500, hostPub, pairSecret = null, hello = hello)
            via = "lan"
            return
        } catch (e: Exception) {
            errors += e
        }
        if (relayHost != null && relayPort != null) {
            try {
                val sid = SessionId.device(hostPub, devicePub)
                connectTcp(relayHost, relayPort, timeoutMs, hostPub, pairSecret = null, hello = hello, sessionId = sid)
                via = "relay"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        if (rdvHosts.isNotEmpty()) {
            try {
                val sid = SessionId.device(hostPub, devicePub)
                connectRendezvous(rdvHosts, sid, hostPub, pairSecret = null, hello = hello, timeoutMs = timeoutMs)
                via = "rdv"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        throw errors.lastOrNull() ?: NoiseException("Could not reach the PC")
    }

    /**
     * `host:port`, `[v6]:port`, bare `[v6]` or bare `v6` (no port). A bracketed
     * literal keeps its inner text; an unbracketed string with more than one
     * colon is a bare IPv6 address, not host:port.
     */
    private fun parseHostPort(raw: String, defaultPort: Int): Pair<String, Int> {
        val t = raw.trim()
        if (t.startsWith("[")) {
            val close = t.indexOf(']')
            if (close < 0) return t.trim('[', ']') to defaultPort
            val host = t.substring(1, close)
            val rest = t.substring(close + 1)
            val port = if (rest.startsWith(":")) rest.substring(1).toIntOrNull() ?: defaultPort else defaultPort
            return host to port
        }
        val colons = t.count { it == ':' }
        if (colons != 1) return t to defaultPort
        val idx = t.lastIndexOf(':')
        val port = t.substring(idx + 1).toIntOrNull() ?: defaultPort
        return t.substring(0, idx) to port
    }

    private fun connectTcp(
        host: String,
        port: Int,
        timeoutMs: Int,
        hostPub: ByteArray,
        pairSecret: ByteArray?,
        hello: ByteArray?,
        sessionId: ByteArray? = null,
    ) {
        val sock = Socket()
        try {
            sock.tcpNoDelay = true
            sock.keepAlive = true
            sock.receiveBufferSize = 64 * 1024
            sock.sendBufferSize = 64 * 1024
            sock.connect(InetSocketAddress(host, port), timeoutMs)
            // A relay/rendezvous splice may accept the TCP connection and then
            // wait minutes for a PC that is offline; bound the handshake read so
            // the app never sits in "Connecting" forever. Transport reads after
            // the handshake are unbounded (SSE streams can be silent for long).
            sock.soTimeout = maxOf(timeoutMs, 5_000)
            val out = sock.getOutputStream()
            val inp = sock.getInputStream()
            if (sessionId != null) {
                out.write(sessionId)
                out.flush()
            }
            // A session-id preamble means an owner relay splice (shared box).
            lenientRecords = sessionId != null
            completeHandshake(inp, out, hostPub, pairSecret, hello) { sock.soTimeout = 0 }
            socket = sock
        } catch (e: Exception) {
            try {
                sock.close()
            } catch (_: Exception) {
            }
            throw e
        }
    }

    /**
     * Noise handshake over any byte streams (TCP socket or MQTT rendezvous).
     * Returns only after the responder's handshake message is verified.
     */
    private fun completeHandshake(
        inp: InputStream,
        out: OutputStream,
        hostPub: ByteArray,
        pairSecret: ByteArray?,
        hello: ByteArray?,
        onHandshook: (() -> Unit)? = null,
    ) {
        val hs = HandshakeState.initiator(localStaticPriv, localStaticPub, hostPub, pairSecret)
        val msg1 = hs.writeMessage(hello ?: ByteArray(0))
        RecordCodec.writeFully(out, RecordCodec.encodeHandshake(msg1))
        val msg2 = RecordCodec.readLengthPrefixed(inp, Protocol.MAX_PLAINTEXT)
        hs.readMessage(msg2)
        // Lift any handshake read deadline before the reader thread starts.
        onHandshook?.invoke()
        input = inp
        output = out
        send = hs.sendCipher()
        recv = hs.recvCipher()
        lastRekeyAt = System.currentTimeMillis()
        startReader()
    }

    /**
     * Mobile-data path with no VPS: dial public MQTT brokers (rendezvous) in
     * order until the Noise handshake succeeds. The broker only ever sees the
     * random session id and ciphertext — the same trust model as an owner relay.
     */
    private fun connectRendezvous(
        brokers: List<Pair<String, Int>>,
        sid: ByteArray,
        hostPub: ByteArray,
        pairSecret: ByteArray?,
        hello: ByteArray?,
        timeoutMs: Int,
    ) {
        var last: Exception? = null
        for ((host, port) in brokers) {
            var client: MqttClient? = null
            try {
                client = MqttClient(host, port, timeoutMs = minOf(timeoutMs, 5_000))
                client.connect()
                val streams = RendezvousStreams(client, sid, role = "phone")
                streams.start()
                lenientRecords = true // public broker: third-party junk is expected
                // Handshake with a deadline: a dead peer must not hang forever.
                var hsError: Exception? = null
                val t = Thread({
                    try {
                        completeHandshake(streams.input, streams.output, hostPub, pairSecret, hello)
                    } catch (e: Exception) {
                        hsError = e
                    }
                }, "grove-rdv-handshake")
                t.isDaemon = true
                t.start()
                t.join(maxOf(timeoutMs.toLong(), 5_000L))
                if (hsError != null) throw hsError as Exception
                if (t.isAlive) {
                    // Close the streams so the abandoned thread fails out
                    // instead of finishing later and overwriting the ciphers
                    // of whichever broker actually won.
                    try {
                        streams.close()
                    } catch (_: Exception) {
                    }
                    throw NoiseException("rendezvous handshake timed out")
                }
                rdv = streams
                socket = null
                return
            } catch (e: Exception) {
                last = e
                try {
                    client?.close()
                } catch (_: Exception) {
                }
            }
        }
        throw last ?: NoiseException("Could not reach a rendezvous broker")
    }

    fun close() {
        closed = true
        try {
            socket?.close()
        } catch (_: Exception) {
        }
        try {
            rdv?.close()
        } catch (_: Exception) {
        }
        reader?.interrupt()
        pending.values.forEach { it.fail(NoiseException("session closed")) }
        pending.clear()
    }

    fun http(
        method: String,
        target: String,
        headers: String = "",
        body: ByteArray = ByteArray(0),
        timeoutMs: Long = 30_000,
    ): HttpFrame.HttpResponse {
        DenyPath.forbiddenReason(target, method)?.let {
            return HttpFrame.HttpResponse(403, "Content-Type: text/plain\r\n", it.toByteArray())
        }
        val id = HttpFrame.nextId()
        val payload = HttpFrame.encodeRequest(HttpFrame.HttpRequest(method, target, headers, body))
        val waiter = Pending()
        pending[id] = waiter
        try {
            for (frame in HttpFrame.fragment(HttpFrame.TYPE_HTTP_REQ, id, payload)) {
                sendInner(frame)
            }
            val raw = waiter.finish(timeoutMs)
            return HttpFrame.decodeResponse(raw)
        } finally {
            pending.remove(id)
            fragments.remove(id)
        }
    }

    fun pipeHttp(
        method: String,
        target: String,
        headers: String,
        body: ByteArray,
        output: OutputStream,
        timeoutMs: Long = 0,
    ) {
        DenyPath.forbiddenReason(target, method)?.let {
            val msg = it.toByteArray()
            output.write(
                "HTTP/1.1 403 Forbidden\r\nContent-Type: text/plain; charset=utf-8\r\nContent-Length: ${msg.size}\r\nConnection: close\r\n\r\n".toByteArray(),
            )
            output.write(msg)
            output.flush()
            return
        }
        val id = HttpFrame.nextId()
        val payload = HttpFrame.encodeRequest(HttpFrame.HttpRequest(method, target, headers, body))
        val waiter = Pending(stream = output)
        pending[id] = waiter
        try {
            for (frame in HttpFrame.fragment(HttpFrame.TYPE_HTTP_REQ, id, payload)) {
                sendInner(frame)
            }
            waiter.awaitDone(if (timeoutMs > 0) timeoutMs else Long.MAX_VALUE)
        } finally {
            pending.remove(id)
            fragments.remove(id)
        }
    }

    private fun maybeRekeyLocked() {
        val s = send ?: return
        val due = s.n >= Protocol.REKEY_AFTER_RECORDS ||
            System.currentTimeMillis() - lastRekeyAt >= Protocol.REKEY_AFTER_MS
        if (!due) return
        val frame = HttpFrame.encode(HttpFrame.TYPE_REKEY, 0, ByteArray(0), fin = true)
        val rec = RecordCodec.encodeTransport(s, frame)
        RecordCodec.writeFully(output!!, rec)
        s.rekey()
        lastRekeyAt = System.currentTimeMillis()
    }

    private fun sendInner(plaintext: ByteArray) {
        synchronized(sendLock) {
            if (closed) throw NoiseException("session closed")
            maybeRekeyLocked()
            val rec = RecordCodec.encodeTransport(send!!, plaintext)
            RecordCodec.writeFully(output!!, rec)
        }
    }

    private fun startReader() {
        val t = Thread({
            // Relay / public rendezvous: anyone who enumerates the broker topic
            // can publish junk. A record that fails the nonce/tag check leaves
            // the cipher untouched, so skip a bounded number instead of
            // tearing the session down. On the LAN a bad record stays fatal.
            val lenient = lenientRecords
            var bad = 0
            try {
                while (!closed) {
                    val body = RecordCodec.readLengthPrefixed(input!!)
                    val pt = try {
                        RecordCodec.decodeTransport(recv!!, body)
                    } catch (e: NoiseException) {
                        bad += 1
                        if (!lenient || bad > MAX_BAD_RECORDS) throw e
                        continue
                    }
                    handleInner(pt)
                }
            } catch (_: Exception) {
                // Any read/decrypt failure ends the session (fail closed) and
                // releases the socket so the controller can dial again.
                closed = true
                pending.values.forEach { it.fail(NoiseException("pipe closed")) }
                pending.clear()
                try {
                    socket?.close()
                } catch (_: Exception) {
                }
                try {
                    rdv?.close()
                } catch (_: Exception) {
                }
            }
        }, "remedy-connect-recv")
        t.isDaemon = true
        t.start()
        reader = t
    }

    private fun handleInner(pt: ByteArray) {
        val hdr = HttpFrame.decode(pt)
        when (hdr.type) {
            HttpFrame.TYPE_REKEY -> {
                recv?.rekey()
            }
            HttpFrame.TYPE_PONG, HttpFrame.TYPE_PING -> {
                if (hdr.type == HttpFrame.TYPE_PING) {
                    sendInner(HttpFrame.encode(HttpFrame.TYPE_PONG, hdr.id, ByteArray(0)))
                }
            }
            HttpFrame.TYPE_HTTP_RES -> {
                val p = pending[hdr.id]
                if (p != null) {
                    p.onChunk(hdr.payload)
                    if (hdr.fin) p.complete()
                } else {
                    if (!fragments.containsKey(hdr.id) && fragments.size >= MAX_UNSOLICITED_RESPONSES) {
                        throw NoiseException("too many unsolicited responses")
                    }
                    val list = fragments.getOrPut(hdr.id) { ArrayList() }
                    if (list.sumOf { it.size.toLong() } + hdr.payload.size > MAX_RESPONSE_BYTES) {
                        fragments.remove(hdr.id)
                        throw NoiseException("response too large")
                    }
                    list += hdr.payload
                    if (hdr.fin) {
                        fragments.remove(hdr.id)
                        val joined = concat(list)
                        for (l in listeners) l(hdr.id, joined)
                    }
                }
            }
            else -> Unit
        }
    }

    private val listeners = CopyOnWriteArrayList<(Int, ByteArray) -> Unit>()

    private class Pending(val stream: OutputStream? = null) {
        private val chunks = ArrayList<ByteArray>()
        private val done = LinkedBlockingQueue<Boolean>(1)
        @Volatile
        var error: Exception? = null
        private var received = 0L

        fun onChunk(b: ByteArray) {
            received += b.size
            if (received > MAX_RESPONSE_BYTES) {
                fail(NoiseException("response too large"))
                return
            }
            try {
                stream?.write(b)
                stream?.flush()
            } catch (e: Exception) {
                fail(e)
                return
            }
            if (stream == null) {
                synchronized(chunks) { chunks += b }
            }
        }

        fun complete() {
            done.offer(true)
        }

        fun fail(e: Exception) {
            error = e
            done.offer(false)
        }

        fun awaitDone(timeoutMs: Long) {
            val ok = done.poll(timeoutMs, TimeUnit.MILLISECONDS)
                ?: throw NoiseException("request timed out")
            if (!ok) throw error ?: NoiseException("pipe closed")
        }

        fun finish(timeoutMs: Long): ByteArray {
            awaitDone(timeoutMs)
            if (error != null) throw error as Exception
            synchronized(chunks) {
                return concat(chunks)
            }
        }
    }

    companion object {
        /** Mirrors the host's MAX_BAD_RECORDS for relay / rendezvous sessions. */
        private const val MAX_BAD_RECORDS = 32
        private const val MAX_RESPONSE_BYTES = 16L * 1024 * 1024
        private const val MAX_UNSOLICITED_RESPONSES = 32

        private fun concat(parts: List<ByteArray>): ByteArray {
            val n = parts.sumOf { it.size }
            val out = ByteArray(n)
            var o = 0
            for (p in parts) {
                System.arraycopy(p, 0, out, o, p.size)
                o += p.size
            }
            return out
        }
    }
}
