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
import computer.iroh.presetMinimal
import computer.iroh.presetN0
import com.remedy.groveconnect.core.Crypto
import com.remedy.groveconnect.core.MqttClient
import com.remedy.groveconnect.core.PunchCrypto
import com.remedy.groveconnect.core.Stun
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import org.json.JSONArray
import org.json.JSONObject
import java.net.DatagramSocket
import java.net.Inet4Address
import java.util.concurrent.LinkedBlockingQueue
import java.util.concurrent.TimeUnit
import java.io.Closeable
import java.io.IOException
import java.io.InputStream
import java.io.OutputStream
import java.net.SocketTimeoutException

/**
 * Where to dial the PC over iroh: its endpoint id and direct addresses
 * (router-mapped public address, IPv6, LAN). By default nothing else: no
 * relay, no lookup service. [relayUrl] is the owner's own iroh-relay;
 * [useN0] only when the PC opted into n0's public relays (advanced).
 */
data class IrohTarget(
    val endpointId: String,
    val relayUrl: String?,
    val addrs: List<String> = emptyList(),
    val useN0: Boolean = false,
    /** MQTT brokers the PC answers hole-punch offers on; empty = punching off. */
    val punchBrokers: List<Pair<String, Int>> = emptyList(),
)

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
    private var endpointPolicy: String? = null
    private var installed = false

    /** Remember the app context. Loads nothing: the native lib loads on first dial. */
    fun init(ctx: Context) {
        appContext = ctx.applicationContext
    }

    /** One endpoint per process, rebound only when the relay policy changes. */
    private fun endpointFor(target: IrohTarget): Endpoint = synchronized(lock) {
        val policy = when {
            target.useN0 -> "n0"
            target.relayUrl != null -> "custom:${target.relayUrl}"
            else -> "off"
        }
        val current = endpoint
        if (current != null && !current.isClosed() && endpointPolicy == policy) return current
        current?.let { runCatching { runBlocking { it.shutdown() } } }
        if (!installed) {
            appContext?.let { IrohAndroid.installAndroidContext(it) }
            installed = true
        }
        val opts = when {
            // Advanced, PC-side opt-in: n0 public relays + n0 address lookup.
            target.useN0 -> EndpointOptions(preset = presetN0())
            // Owner's own relay only; no n0 lookup.
            target.relayUrl != null -> EndpointOptions(
                preset = presetMinimal(),
                relayMode = RelayMode.customFromUrls(listOf(target.relayUrl)),
            )
            // Default: direct to the PC's own addresses, no third party at all.
            else -> EndpointOptions(preset = presetMinimal(), relayMode = RelayMode.disabled())
        }
        val ep = runBlocking { Endpoint.bind(opts) }
        endpoint = ep
        endpointPolicy = policy
        ep
    }

    /** Dial [target] and open one bidirectional stream for a Noise session. */
    fun open(target: IrohTarget, timeoutMs: Long): IrohPipe {
        val ep = endpointFor(target)
        val started = System.nanoTime()
        return runBlocking {
            withTimeout(timeoutMs) {
                val id = EndpointId.fromString(target.endpointId)
                val addr = EndpointAddr(id, target.relayUrl, target.addrs)
                val conn = ep.connect(addr, ALPN)
                val bi = conn.openBi()
                IrohPipe(conn, bi.send(), bi.recv(), dialMs = (System.nanoTime() - started) / 1_000_000)
            }
        }
    }

    /**
     * Relay-free hole punch through carrier-grade NAT. Learn this phone's
     * public mapping with STUN on a fresh UDP port, bind a one-off endpoint on
     * that same port (so it inherits the mapping), swap encrypted addresses
     * with the PC over a public MQTT broker, then dial while the PC sends
     * toward us. The brokers see only ciphertext; Noise still pins the PC.
     */
    fun openPunched(target: IrohTarget, hostPub: ByteArray, timeoutMs: Long): IrohPipe {
        if (target.punchBrokers.isEmpty()) throw IOException("hole punching is off on the PC")
        synchronized(lock) {
            if (!installed) {
                appContext?.let { IrohAndroid.installAndroidContext(it) }
                installed = true
            }
        }
        val started = System.nanoTime()
        val sock = DatagramSocket(0)
        val port = sock.localPort
        val mapped = try {
            Stun.mapped(sock)
        } finally {
            sock.close()
        }
        android.util.Log.i("RemedyConnect", "punch STUN after ${(System.nanoTime() - started) / 1_000_000}ms")
        val ep = runBlocking {
            Endpoint.bind(
                EndpointOptions(
                    preset = presetMinimal(),
                    relayMode = RelayMode.disabled(),
                    bindAddr = "0.0.0.0:$port",
                ),
            )
        }
        try {
            val mine = ArrayList<String>()
            mine += "${mapped.address.hostAddress}:${mapped.port}"
            for (a in ep.addr().directAddresses()) {
                if (!a.startsWith("[") && a !in mine) mine += a
            }
            val nonce = Crypto.randomBytes(8).joinToString("") { "%02x".format(it) }
            val offer = JSONObject()
                .put("v", 1)
                .put("from", ep.id().toString())
                .put("addrs", JSONArray(mine))
                .put("nonce", nonce)
                .put("ts", System.currentTimeMillis() / 1000)
                .toString()
            val pcAddrs = swapAddresses(target.punchBrokers, hostPub, nonce, offer)
            val pipe = runBlocking {
                withTimeout(timeoutMs) {
                    val addr = EndpointAddr(EndpointId.fromString(target.endpointId), null, pcAddrs)
                    val conn = ep.connect(addr, ALPN)
                    val bi = conn.openBi()
                    IrohPipe(conn, bi.send(), bi.recv(), dialMs = (System.nanoTime() - started) / 1_000_000, owned = ep)
                }
            }
            android.util.Log.i("RemedyConnect", "iroh hole punch: me=${mine.first()} pc=${pcAddrs.firstOrNull()}")
            return pipe
        } catch (e: Exception) {
            runCatching { runBlocking { ep.shutdown() } }
            throw e
        }
    }

    /**
     * Publish the sealed offer on every broker at once; the first valid answer
     * wins. A slow or dead public broker then costs nothing.
     */
    private fun swapAddresses(
        brokers: List<Pair<String, Int>>,
        hostPub: ByteArray,
        nonce: String,
        offer: String,
    ): List<String> {
        val result = LinkedBlockingQueue<Result<List<String>>>()
        val clients = java.util.concurrent.CopyOnWriteArrayList<MqttClient>()
        val sealed = PunchCrypto.seal(hostPub, offer)
        val t0 = System.nanoTime()
        for ((host, port) in brokers) {
            Thread({
                val client = MqttClient(host, port, timeoutMs = 5_000)
                clients += client
                try {
                    client.onMessage = { _, payload ->
                        val ans = try {
                            JSONObject(PunchCrypto.open(hostPub, payload))
                        } catch (_: Exception) {
                            null // junk on a public topic
                        }
                        if (ans != null && ans.optString("nonce") == nonce) {
                            val err = ans.optString("error")
                            if (err.isNotEmpty()) {
                                result.offer(Result.failure(IOException("PC could not punch: $err")))
                            } else {
                                val arr = ans.optJSONArray("addrs") ?: JSONArray()
                                val out = (0 until minOf(arr.length(), 8)).map { arr.getString(it) }
                                if (out.isNotEmpty()) {
                                    android.util.Log.i(
                                        "RemedyConnect",
                                        "punch answer via $host after ${(System.nanoTime() - t0) / 1_000_000}ms",
                                    )
                                    result.offer(Result.success(out))
                                }
                            }
                        }
                    }
                    client.connect()
                    client.subscribe(listOf(PunchCrypto.replyTopic(hostPub, nonce)))
                    client.publish(PunchCrypto.topic(hostPub), sealed)
                    android.util.Log.i(
                        "RemedyConnect",
                        "punch offer on $host after ${(System.nanoTime() - t0) / 1_000_000}ms",
                    )
                } catch (e: Exception) {
                    android.util.Log.i("RemedyConnect", "punch broker $host failed: ${e.message}")
                }
            }, "remedy-punch-$host").apply { isDaemon = true }.start()
        }
        try {
            val r = result.poll(10_000, TimeUnit.MILLISECONDS) ?: throw IOException("no answer from the PC on any broker")
            return r.getOrThrow()
        } finally {
            for (c in clients) runCatching { c.close() }
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
    /** A one-off endpoint (hole punch) this pipe owns and closes. */
    private val owned: Endpoint? = null,
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
        owned?.let { runCatching { runBlocking { it.shutdown() } } }
    }

    private companion object {
        val READ_CHUNK = (64 * 1024).toUInt()
    }
}
