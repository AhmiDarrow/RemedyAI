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

    fun connect(qr: QrPayload, timeoutMs: Int = 8_000, preferRelay: Boolean = false) {
        val errors = ArrayList<Exception>()
        val lanMs = 1_500
        val relayHost = qr.relayHost
        val relayPort = qr.relayPort
        val rdvHosts = qr.rdvHosts
        val tsHost = qr.tailscaleHost
        val tsPort = qr.tailscalePort
        // Tailscale first: the tailnet IP works on Wi-Fi AND mobile data
        // (DERP relays handle NAT), so it is the universal path when present.
        if (tsHost != null && tsPort != null) {
            try {
                connectTcp(tsHost, tsPort, timeoutMs, qr.hostPub, qr.pairSecret, hello = null)
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
                    connectTcp(relayHost, relayPort, timeoutMs, qr.hostPub, qr.pairSecret, hello = null, sessionId = sid)
                    via = "relay"
                    return
                } catch (e: Exception) {
                    errors += e
                }
            }
            if (rdvHosts.isNotEmpty()) {
                try {
                    val sid = SessionId.pair(qr.hostPub, qr.pairSecret)
                    connectRendezvous(rdvHosts, sid, qr.hostPub, qr.pairSecret, hello = null, timeoutMs = timeoutMs)
                    via = "rdv"
                    return
                } catch (e: Exception) {
                    errors += e
                }
            }
            throw errors.lastOrNull() ?: NoiseException("Could not reach the PC")
        }
        try {
            connectTcp(qr.lanHost, qr.lanPort, lanMs, qr.hostPub, qr.pairSecret, hello = null)
            via = "lan"
            return
        } catch (e: Exception) {
            errors += e
        }
        val v6 = qr.v6
        if (!v6.isNullOrBlank()) {
            try {
                val (h, p) = parseHostPort(v6, qr.lanPort)
                connectTcp(h, p, lanMs, qr.hostPub, qr.pairSecret, hello = null)
                via = "v6"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        if (relayHost != null && relayPort != null) {
            try {
                val sid = SessionId.pair(qr.hostPub, qr.pairSecret)
                connectTcp(relayHost, relayPort, timeoutMs, qr.hostPub, qr.pairSecret, hello = null, sessionId = sid)
                via = "relay"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        if (rdvHosts.isNotEmpty()) {
            try {
                val sid = SessionId.pair(qr.hostPub, qr.pairSecret)
                connectRendezvous(rdvHosts, sid, qr.hostPub, qr.pairSecret, hello = null, timeoutMs = timeoutMs)
                via = "rdv"
                return
            } catch (e: Exception) {
                errors += e
            }
        }
        throw errors.lastOrNull() ?: NoiseException("Could not reach the PC")
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
        // Tailscale first: universal path (Wi-Fi + mobile data).
        if (tailscaleHost != null && tailscalePort != null) {
            try {
                val sid = SessionId.device(hostPub, devicePub)
                connectTcp(tailscaleHost, tailscalePort, timeoutMs, hostPub, pairSecret = null, hello = hello, sessionId = sid)
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

    private fun parseHostPort(raw: String, defaultPort: Int): Pair<String, Int> {
        val t = raw.trim().trim('[', ']')
        val idx = t.lastIndexOf(':')
        if (idx <= 0) return t to defaultPort
        val port = t.substring(idx + 1).toIntOrNull() ?: defaultPort
        var host = t.substring(0, idx)
        if (host.startsWith("[")) host = host.trim('[', ']')
        return host to port
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
            sock.soTimeout = 0
            val out = sock.getOutputStream()
            val inp = sock.getInputStream()
            if (sessionId != null) {
                out.write(sessionId)
                out.flush()
            }
            completeHandshake(inp, out, hostPub, pairSecret, hello)
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
    ) {
        val hs = HandshakeState.initiator(localStaticPriv, localStaticPub, hostPub, pairSecret)
        val msg1 = hs.writeMessage(hello ?: ByteArray(0))
        RecordCodec.writeFully(out, RecordCodec.encodeHandshake(msg1))
        val msg2 = RecordCodec.readLengthPrefixed(inp, Protocol.MAX_PLAINTEXT)
        hs.readMessage(msg2)
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
                if (t.isAlive) throw NoiseException("rendezvous handshake timed out")
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
            try {
                while (!closed) {
                    val body = RecordCodec.readLengthPrefixed(input!!)
                    val pt = RecordCodec.decodeTransport(recv!!, body)
                    handleInner(pt)
                }
            } catch (_: Exception) {
                closed = true
                pending.values.forEach { it.fail(NoiseException("pipe closed")) }
            }
        }, "grove-connect-recv")
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
                    val list = fragments.getOrPut(hdr.id) { ArrayList() }
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

        fun onChunk(b: ByteArray) {
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
