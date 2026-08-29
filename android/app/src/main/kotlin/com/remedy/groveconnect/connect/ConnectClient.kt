package com.remedy.groveconnect.connect

import com.remedy.groveconnect.core.CipherState
import com.remedy.groveconnect.core.DenyPath
import com.remedy.groveconnect.core.HandshakeState
import com.remedy.groveconnect.core.HttpFrame
import com.remedy.groveconnect.core.NoiseException
import com.remedy.groveconnect.core.Protocol
import com.remedy.groveconnect.core.QrPayload
import com.remedy.groveconnect.core.RecordCodec
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

    /** How this session reached the PC: lan, v6, or relay. */
    @Volatile
    var via: String = "lan"
        private set

    fun connect(qr: QrPayload, timeoutMs: Int = 8_000) {
        val errors = ArrayList<Exception>()
        val lanMs = 1_500
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
        val rh = qr.relayHost
        val rp = qr.relayPort
        if (rh != null && rp != null) {
            val sid = SessionId.pair(qr.hostPub, qr.pairSecret)
            connectTcp(rh, rp, timeoutMs, qr.hostPub, qr.pairSecret, hello = null, sessionId = sid)
            via = "relay"
            return
        }
        throw errors.lastOrNull() ?: NoiseException("Could not reach the PC")
    }

    fun reconnect(
        hostPub: ByteArray,
        lanHost: String,
        lanPort: Int,
        relayHost: String?,
        relayPort: Int?,
        devicePub: ByteArray,
        timeoutMs: Int = 8_000,
    ) {
        val hello = ("hello\u0000" + SessionId.deviceIdHex(devicePub)).toByteArray(Charsets.UTF_8)
        val errors = ArrayList<Exception>()
        try {
            connectTcp(lanHost, lanPort, 1_500, hostPub, pairSecret = null, hello = hello)
            via = "lan"
            return
        } catch (e: Exception) {
            errors += e
        }
        if (relayHost != null && relayPort != null) {
            val sid = SessionId.device(hostPub, devicePub)
            connectTcp(relayHost, relayPort, timeoutMs, hostPub, pairSecret = null, hello = hello, sessionId = sid)
            via = "relay"
            return
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
            sock.connect(InetSocketAddress(host, port), timeoutMs)
            sock.soTimeout = 0
            val out = sock.getOutputStream()
            val inp = sock.getInputStream()
            if (sessionId != null) {
                out.write(sessionId)
                out.flush()
            }
            val hs = HandshakeState.initiator(localStaticPriv, localStaticPub, hostPub, pairSecret)
            val msg1 = hs.writeMessage(hello ?: ByteArray(0))
            RecordCodec.writeFully(out, RecordCodec.encodeHandshake(msg1))
            val msg2 = RecordCodec.readLengthPrefixed(inp, Protocol.MAX_PLAINTEXT)
            hs.readMessage(msg2)
            socket = sock
            input = inp
            output = out
            send = hs.sendCipher()
            recv = hs.recvCipher()
            lastRekeyAt = System.currentTimeMillis()
            startReader()
        } catch (e: Exception) {
            try {
                sock.close()
            } catch (_: Exception) {
            }
            throw e
        }
    }

    fun close() {
        closed = true
        try {
            socket?.close()
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
