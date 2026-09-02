package com.remedy.groveconnect.connect

import com.remedy.groveconnect.core.DenyPath
import com.remedy.groveconnect.core.RecordCodec
import com.remedy.groveconnect.core.ShimAuth
import java.io.InputStream
import java.io.OutputStream
import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.net.SocketTimeoutException
import java.util.Collections
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.RejectedExecutionException
import java.util.concurrent.ThreadPoolExecutor
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Tiny loopback HTTP reverse proxy. WebView talks here; we encrypt to the PC.
 *
 * Auth is a high-entropy path prefix (or cookie/header after a token-bearing
 * request). Unauthenticated GET / is 403 and must not Set-Cookie.
 */
fun interface ShimPipe {
    fun pipeHttp(method: String, target: String, headers: String, body: ByteArray, output: OutputStream)
}

class LoopbackShim(
    private val headerDeadlineMs: Int = HEADER_TIMEOUT_MS,
    private val pipe: ShimPipe,
) {
    constructor(client: ConnectClient) : this(
        pipe = ShimPipe { method, target, headers, body, output ->
            client.pipeHttp(method, target, headers, body, output)
        },
    )

    private val running = AtomicBoolean(false)
    private var server: ServerSocket? = null
    private val shimToken: String = ShimAuth.newToken()
    private val clients = Collections.synchronizedSet(mutableSetOf<Socket>())
    private val pool = ThreadPoolExecutor(
        2, MAX_CLIENTS, 30, TimeUnit.SECONDS, ArrayBlockingQueue(MAX_PENDING),
        { r -> Thread(r, "grove-shim").apply { isDaemon = true } },
    )

    val port: Int get() = server?.localPort ?: 0
    val token: String get() = shimToken

    fun webViewUrl(): String = ShimAuth.webViewUrl(port, shimToken)

    fun start(): Int {
        val ss = ServerSocket(0, 50, InetAddress.getByName("127.0.0.1"))
        server = ss
        running.set(true)
        Thread({
            while (running.get()) {
                try {
                    val sock = ss.accept()
                    sock.soTimeout = HEADER_TIMEOUT_MS
                    clients += sock
                    try {
                        pool.execute { handle(sock) }
                    } catch (_: RejectedExecutionException) {
                        clients -= sock
                        sock.close()
                    }
                } catch (_: Exception) {
                    if (!running.get()) break
                }
            }
        }, "grove-shim-accept").apply {
            isDaemon = true
            start()
        }
        return ss.localPort
    }

    fun stop() {
        running.set(false)
        try {
            server?.close()
        } catch (_: Exception) {
        }
        synchronized(clients) {
            clients.toList().forEach {
                try {
                    it.close()
                } catch (_: Exception) {
                }
            }
            clients.clear()
        }
        pool.shutdownNow()
    }

    private fun handle(sock: Socket) {
        sock.use { s ->
            try {
                val input = s.getInputStream()
                val output = s.getOutputStream()
                val req = readRequest(s, input) ?: return
                if (!ShimAuth.allowed(req.target, req.headers, shimToken)) {
                    writeResponse(output, 403, "text/plain; charset=utf-8", "shim".toByteArray())
                    return
                }
                val destTarget = ShimAuth.strip(req.target, shimToken)
                if (DenyPath.isForbidden(destTarget, req.method)) {
                    writeResponse(
                        output,
                        403,
                        "text/plain; charset=utf-8",
                        (DenyPath.forbiddenReason(destTarget) ?: "forbidden").toByteArray(),
                    )
                    return
                }
                val dest = HeaderInjectingOutput(output, ShimAuth.cookieValue(shimToken))
                pipe.pipeHttp(req.method, destTarget, req.headers, req.body, dest)
            } catch (e: BodyTooLarge) {
                try {
                    val msg = "Request body is ${e.size} bytes; the phone remote accepts up to $MAX_BODY.".toByteArray()
                    writeResponse(s.getOutputStream(), 413, "text/plain; charset=utf-8", msg)
                } catch (_: Exception) {
                }
            } catch (_: HeaderTooLarge) {
                try {
                    writeResponse(s.getOutputStream(), 431, "text/plain; charset=utf-8", "Request headers are too large.".toByteArray())
                } catch (_: Exception) {
                }
            } catch (_: HeaderTimedOut) {
                try {
                    writeResponse(s.getOutputStream(), 408, "text/plain; charset=utf-8", "Request headers timed out.".toByteArray())
                } catch (_: Exception) {
                }
            } catch (e: BadRequest) {
                try {
                    writeResponse(s.getOutputStream(), 400, "text/plain; charset=utf-8", e.message.orEmpty().toByteArray())
                } catch (_: Exception) {
                }
            } catch (e: Exception) {
                try {
                    val msg = (e.message ?: "pipe error").toByteArray()
                    writeResponse(s.getOutputStream(), 502, "text/plain; charset=utf-8", msg)
                } catch (_: Exception) {
                }
            } finally {
                clients -= s
            }
        }
    }

    private data class ShimReq(val method: String, val target: String, val headers: String, val body: ByteArray)

    /** Refuse instead of forwarding a silently truncated body. */
    private class BodyTooLarge(val size: Int) : Exception("request body too large")
    private class HeaderTooLarge : Exception("request headers too large")
    private class HeaderTimedOut : Exception("request headers timed out")
    private class BadRequest(message: String) : Exception(message)

    private companion object {
        /** Matches the PC pipe's MAX_BODY for raw requests. */
        const val MAX_BODY = 1024 * 1024
        const val MAX_CLIENTS = 8
        const val MAX_PENDING = 16
        const val HEADER_TIMEOUT_MS = 10_000
    }

    private fun readRequest(sock: Socket, input: InputStream): ShimReq? {
        val headerBlock = readHeaders(sock, input) ?: return null
        // Header reads tighten SO_TIMEOUT to the absolute time remaining.
        // Restore the normal inactivity budget before reading a legitimate body.
        sock.soTimeout = HEADER_TIMEOUT_MS
        val text = headerBlock.toString(Charsets.ISO_8859_1)
        val lines = text.split("\r\n")
        if (lines.isEmpty()) return null
        val parts = lines[0].split(" ")
        if (parts.size < 2) return null
        val method = parts[0]
        val target = parts[1]
        val headers = lines.drop(1).filter { it.isNotBlank() }
        if (headers.any { it.startsWith("Transfer-Encoding:", ignoreCase = true) }) {
            throw BadRequest("Transfer-Encoding is not accepted by the phone remote.")
        }
        val contentLengths = headers.filter { it.startsWith("Content-Length:", ignoreCase = true) }
            .map { it.substringAfter(":").trim().toIntOrNull() ?: throw BadRequest("Invalid Content-Length.") }
        if (contentLengths.distinct().size > 1) throw BadRequest("Conflicting Content-Length headers.")
        val cl = contentLengths.firstOrNull() ?: 0
        if (cl < 0) throw BadRequest("Invalid Content-Length.")
        if (cl > MAX_BODY) throw BodyTooLarge(cl)
        val body = if (cl > 0) RecordCodec.readExact(input, cl) else ByteArray(0)
        val forwarded = headers.filterNot {
            it.startsWith("Host:", ignoreCase = true) ||
                it.startsWith("Content-Length:", ignoreCase = true) ||
                it.startsWith("Connection:", ignoreCase = true)
        }.joinToString("\r\n")
        return ShimReq(method, target, forwarded, body)
    }

    private fun readHeaders(sock: Socket, input: InputStream): ByteArray? {
        val buf = java.io.ByteArrayOutputStream(512)
        val deadline = System.nanoTime() + TimeUnit.MILLISECONDS.toNanos(headerDeadlineMs.toLong())
        // Track the last four bytes so we never re-scan the whole buffer.
        var tail = 0
        var n = 0
        while (n < 64 * 1024) {
            val remainingNanos = deadline - System.nanoTime()
            if (remainingNanos <= 0) throw HeaderTimedOut()
            sock.soTimeout = TimeUnit.NANOSECONDS.toMillis(remainingNanos)
                .coerceAtLeast(1)
                .coerceAtMost(Int.MAX_VALUE.toLong())
                .toInt()
            val c = try {
                input.read()
            } catch (_: SocketTimeoutException) {
                throw HeaderTimedOut()
            }
            if (c < 0) {
                if (buf.size() == 0) return null
                throw BadRequest("Incomplete request headers.")
            }
            buf.write(c)
            n++
            tail = (tail shl 8) or (c and 0xff)
            if (tail == 0x0d0a0d0a) return buf.toByteArray()
        }
        throw HeaderTooLarge()
    }

    /** Rewrite the first HTTP header block to mint the shim cookie (authenticated only). */
    private class HeaderInjectingOutput(
        private val dest: OutputStream,
        private val setCookie: String,
    ) : OutputStream() {
        private val buf = java.io.ByteArrayOutputStream(512)
        private var headersDone = false

        override fun write(b: Int) {
            write(byteArrayOf(b.toByte()))
        }

        override fun write(b: ByteArray, off: Int, len: Int) {
            if (headersDone) {
                dest.write(b, off, len)
                return
            }
            buf.write(b, off, len)
            val bytes = buf.toByteArray()
            val idx = indexOfHeaderEnd(bytes) ?: return
            val head = bytes.copyOfRange(0, idx).toString(Charsets.ISO_8859_1)
            val rest = bytes.copyOfRange(idx + 4, bytes.size)
            // The PC strips Connection headers and this shim closes the socket
            // after one response, so say so: otherwise the client pools the
            // connection and a streamed POST dies with "unexpected end of stream".
            val lines = head.split("\r\n").filterNot { it.startsWith("Connection:", ignoreCase = true) }
            val base = lines.joinToString("\r\n") + "\r\nConnection: close"
            val injected = if (head.contains("Set-Cookie:", ignoreCase = true)) {
                "$base\r\n\r\n"
            } else {
                "$base\r\nSet-Cookie: $setCookie\r\n\r\n"
            }
            dest.write(injected.toByteArray(Charsets.ISO_8859_1))
            if (rest.isNotEmpty()) dest.write(rest)
            headersDone = true
            buf.reset()
        }

        override fun flush() {
            dest.flush()
        }

        private fun indexOfHeaderEnd(bytes: ByteArray): Int? {
            if (bytes.size < 4) return null
            for (i in 0..bytes.size - 4) {
                if (bytes[i] == '\r'.code.toByte() &&
                    bytes[i + 1] == '\n'.code.toByte() &&
                    bytes[i + 2] == '\r'.code.toByte() &&
                    bytes[i + 3] == '\n'.code.toByte()
                ) {
                    return i
                }
            }
            return null
        }
    }

    private fun writeResponse(
        out: OutputStream,
        status: Int,
        contentType: String,
        body: ByteArray,
        extraHeaders: String = "",
        setCookie: String? = null,
    ) {
        val reason = when (status) {
            200 -> "OK"
            400 -> "Bad Request"
            403 -> "Forbidden"
            408 -> "Request Timeout"
            404 -> "Not Found"
            413 -> "Payload Too Large"
            431 -> "Request Header Fields Too Large"
            502 -> "Bad Gateway"
            else -> "OK"
        }
        val skip = setOf(
            "content-length", "connection", "transfer-encoding",
            "authorization", "www-authenticate", "set-cookie",
        )
        val extra = extraHeaders.lineSequence()
            .filter { it.contains(':') }
            .filter { it.substringBefore(':').trim().lowercase() !in skip }
            .joinToString("\r\n")
        val sb = StringBuilder()
        sb.append("HTTP/1.1 ").append(status).append(' ').append(reason).append("\r\n")
        sb.append("Content-Type: ").append(contentType).append("\r\n")
        sb.append("Content-Length: ").append(body.size).append("\r\n")
        sb.append("Connection: close\r\n")
        if (!setCookie.isNullOrBlank()) {
            sb.append("Set-Cookie: ").append(setCookie).append("\r\n")
        }
        if (extra.isNotEmpty()) sb.append(extra).append("\r\n")
        sb.append("\r\n")
        out.write(sb.toString().toByteArray(Charsets.ISO_8859_1))
        out.write(body)
        out.flush()
    }
}
