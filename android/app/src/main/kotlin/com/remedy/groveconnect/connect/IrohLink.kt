package com.remedy.groveconnect.connect

import android.content.Context
import computer.iroh.Connection
import computer.iroh.Endpoint
import computer.iroh.EndpointAddr
import computer.iroh.EndpointId
import computer.iroh.EndpointOptions
import computer.iroh.IrohAndroid
import computer.iroh.RecvStream
import computer.iroh.RelayMode
import computer.iroh.SendStream
import computer.iroh.presetN0
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import java.io.Closeable
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.SocketTimeoutException

/** Where to dial the PC over iroh: its endpoint id, plus the owner's relay if any. */
data class IrohTarget(val endpointId: String, val relayUrl: String?)

/**
 * iroh transport (spike): dial the PC by public key over QUIC, with hole
 * punching and n0's free public relays (or the owner's iroh-relay) as the
 * fallback. No account, no VPN app.
 *
 * iroh only carries bytes. [ConnectClient] runs the same Noise IK handshake
 * against the pinned host key over [IrohPipe.input]/[IrohPipe.output] as it
 * does over TCP, so a relay sees IPs and timing but never content, and a
 * forged endpoint id cannot impersonate the PC.
 */
object IrohLink {
    private val ALPN = "remedy-connect/1".toByteArray(Charsets.US_ASCII)

    @Volatile
    private var appContext: Context? = null
    private val lock = Any()
    private var endpoint: Endpoint? = null
    private var endpointRelay: String? = null
    private var installed = false

    /** Remember the app context. Loads nothing: the native lib loads on first dial. */
    fun init(ctx: Context) {
        appContext = ctx.applicationContext
    }

    /** One endpoint per process, rebound only when the relay choice changes. */
    private fun endpointFor(relayUrl: String?): Endpoint = synchronized(lock) {
        val current = endpoint
        if (current != null && !current.isClosed() && endpointRelay == relayUrl) return current
        current?.let { runCatching { runBlocking { it.shutdown() } } }
        if (!installed) {
            appContext?.let { IrohAndroid.installAndroidContext(it) }
            installed = true
        }
        val opts = EndpointOptions(
            preset = presetN0(),
            relayMode = relayUrl?.let { RelayMode.customFromUrls(listOf(it)) },
        )
        val ep = runBlocking { Endpoint.bind(opts) }
        endpoint = ep
        endpointRelay = relayUrl
        ep
    }

    /** Dial [target] and open one bidirectional stream for a Noise session. */
    fun open(target: IrohTarget, timeoutMs: Long): IrohPipe {
        val ep = endpointFor(target.relayUrl)
        val started = System.nanoTime()
        return runBlocking {
            withTimeout(timeoutMs) {
                val id = EndpointId.fromString(target.endpointId)
                val addr = EndpointAddr(id, target.relayUrl, emptyList())
                val conn = ep.connect(addr, ALPN)
                val bi = conn.openBi()
                IrohPipe(conn, bi.send(), bi.recv(), dialMs = (System.nanoTime() - started) / 1_000_000)
            }
        }
    }

    /** Close the shared endpoint (app shutdown). */
    fun shutdown() = synchronized(lock) {
        endpoint?.let { runCatching { runBlocking { it.shutdown() } } }
        endpoint = null
    }
}

/** One iroh bidirectional stream as blocking Java streams. */
class IrohPipe internal constructor(
    private val conn: Connection,
    private val send: SendStream,
    private val recv: RecvStream,
    /** Time from dial to an open stream, for the transport log. */
    val dialMs: Long,
) : Closeable {
    /** Read deadline in ms, 0 = none. Bounds the Noise handshake like soTimeout. */
    @Volatile
    var readTimeoutMs: Long = 0

    @Volatile
    private var closed = false

    /** "direct" or "relay" for the path QUIC currently uses, plus its RTT. */
    fun path(): Pair<String, Long> {
        val p = runCatching { conn.paths().firstOrNull { it.isSelected } }.getOrNull()
            ?: return "none" to 0L
        return (if (p.isRelay) "relay" else "direct") to p.rttMs.toLong()
    }

    val input: InputStream = object : InputStream() {
        private var buf = ByteArray(0)
        private var pos = 0

        override fun read(): Int {
            val one = ByteArray(1)
            val n = read(one, 0, 1)
            return if (n <= 0) -1 else one[0].toInt() and 0xff
        }

        override fun read(b: ByteArray, off: Int, len: Int): Int {
            if (len == 0) return 0
            if (pos >= buf.size) {
                if (closed) return -1
                val deadline = readTimeoutMs
                val chunk = try {
                    runBlocking {
                        if (deadline > 0) {
                            withTimeout(deadline) { recv.read(READ_CHUNK) }
                        } else {
                            recv.read(READ_CHUNK)
                        }
                    }
                } catch (e: kotlinx.coroutines.TimeoutCancellationException) {
                    throw SocketTimeoutException("iroh read timed out")
                } catch (e: Exception) {
                    throw IOException("iroh read failed", e)
                }
                if (chunk.isEmpty()) return -1
                buf = chunk
                pos = 0
            }
            val n = minOf(len, buf.size - pos)
            System.arraycopy(buf, pos, b, off, n)
            pos += n
            return n
        }
    }

    val output: OutputStream = object : OutputStream() {
        override fun write(b: Int) = write(byteArrayOf(b.toByte()), 0, 1)

        override fun write(b: ByteArray, off: Int, len: Int) {
            if (closed) throw IOException("iroh stream closed")
            try {
                runBlocking { send.writeAll(b.copyOfRange(off, off + len)) }
            } catch (e: Exception) {
                throw IOException("iroh write failed", e)
            }
        }
    }

    override fun close() {
        if (closed) return
        closed = true
        runCatching { runBlocking { send.finish() } }
        runCatching { conn.close(0L, "bye".toByteArray()) }
    }

    private companion object {
        val READ_CHUNK = (64 * 1024).toUInt()
    }
}
