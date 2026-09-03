package com.remedy.groveconnect.connect

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test
import java.net.Socket
import java.util.concurrent.CopyOnWriteArrayList
import kotlin.concurrent.thread

class LoopbackShimTest {
    private val unauthenticated = listOf(
        "GET / HTTP/1.1",
        "HEAD / HTTP/1.1",
        "GET /index.html HTTP/1.1",
        "GET /assets/index-abc.js HTTP/1.1",
        "GET /?connect=1 HTTP/1.1",
        "GET /api/chat HTTP/1.1",
        "GET /api/settings HTTP/1.1",
        "POST /api/chat HTTP/1.1",
        "GET /api/connect HTTP/1.1",
    )

    @Test
    fun unauthenticatedLocalGetDoesNotMintCookie() {
        val forwarded = CopyOnWriteArrayList<String>()
        val shim = LoopbackShim { _, target, _, _, output ->
            forwarded += target
            writeOk(output)
        }
        val port = shim.start()
        try {
            for (line in unauthenticated) {
                val res = exchange(port, "$line\r\nHost: 127.0.0.1\r\n\r\n")
                assertTrue("expected 403 for $line, got:\n$res", res.startsWith("HTTP/1.1 403"))
                assertFalse("must not Set-Cookie for $line:\n$res", hasSetCookie(res))
            }
            assertTrue("pipe must not run for unauthenticated GETs", forwarded.isEmpty())
        } finally {
            shim.stop()
        }
    }

    @Test
    fun pathPrefixAllowsCompactSpaAndMintsCookieOnlyThen() {
        val forwarded = CopyOnWriteArrayList<String>()
        val shim = LoopbackShim { _, target, _, _, output ->
            forwarded += target
            writeOk(output)
        }
        val port = shim.start()
        try {
            val url = shim.webViewUrl()
            assertEquals("http://127.0.0.1:$port/${shim.token}/?connect=1", url)

            val authed = exchange(port, "GET /${shim.token}/?connect=1 HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")
            assertTrue(authed.startsWith("HTTP/1.1 200"))
            assertTrue(hasSetCookie(authed))
            assertTrue(authed.contains("grove_shim=${shim.token}"))
            assertEquals(listOf("/?connect=1"), forwarded.toList())

            val cookieReq = exchange(
                port,
                "GET /api/chat HTTP/1.1\r\nHost: 127.0.0.1\r\nCookie: grove_shim=${shim.token}\r\n\r\n",
            )
            assertTrue(cookieReq.startsWith("HTTP/1.1 200"))
            assertEquals("/api/chat", forwarded.last())

            val headerReq = exchange(
                port,
                "GET /assets/index-abc.js HTTP/1.1\r\nHost: 127.0.0.1\r\nX-Grove-Shim: ${shim.token}\r\n\r\n",
            )
            assertTrue(headerReq.startsWith("HTTP/1.1 200"))
            assertEquals("/assets/index-abc.js", forwarded.last())
        } finally {
            shim.stop()
        }
    }

    @Test
    fun rejectsUnterminatedHeadersAndInvalidLengths() {
        val shim = LoopbackShim { _, _, _, _, output -> writeOk(output) }
        val port = shim.start()
        try {
            val oversized = "GET / HTTP/1.1\r\nX-Fill: " + "x".repeat(64 * 1024)
            assertTrue(exchange(port, oversized).startsWith("HTTP/1.1 431"))
            val negative = "POST /${shim.token}/ HTTP/1.1\r\nContent-Length: -1\r\n\r\n"
            assertTrue(exchange(port, negative).startsWith("HTTP/1.1 400"))
            val conflicting = "POST /${shim.token}/ HTTP/1.1\r\nContent-Length: 0\r\nContent-Length: 1\r\n\r\n"
            assertTrue(exchange(port, conflicting).startsWith("HTTP/1.1 400"))
        } finally {
            shim.stop()
        }
    }

    @Test
    fun trickledHeadersCannotExtendTheAbsoluteDeadline() {
        val shim = LoopbackShim(
            pipe = ShimPipe { _, _, _, _, output -> writeOk(output) },
            headerDeadlineMs = 150,
        )
        val port = shim.start()
        try {
            Socket("127.0.0.1", port).use { sock ->
                sock.soTimeout = 2_000
                val sender = thread(isDaemon = true) {
                    try {
                        val output = sock.getOutputStream()
                        for (byte in "GET /${shim.token}/ HTTP/1.1\r\n".toByteArray()) {
                            output.write(byte.toInt())
                            output.flush()
                            Thread.sleep(30)
                        }
                    } catch (_: Exception) {
                        // Expected once the deadline closes the connection.
                    }
                }
                val response = sock.getInputStream().readBytes().toString(Charsets.ISO_8859_1)
                sender.join(1_000)
                assertTrue(response.startsWith("HTTP/1.1 408"))
            }
        } finally {
            shim.stop()
        }
    }

    @Test
    fun trickledBodyCannotExtendTheAbsoluteDeadline() {
        val shim = LoopbackShim(
            bodyDeadlineMs = 150,
            pipe = ShimPipe { _, _, _, _, output -> writeOk(output) },
        )
        val port = shim.start()
        try {
            Socket("127.0.0.1", port).use { sock ->
                sock.soTimeout = 2_000
                val output = sock.getOutputStream()
                output.write(
                    "POST /${shim.token}/ HTTP/1.1\r\nContent-Length: 64\r\n\r\n".toByteArray(),
                )
                output.flush()
                val sender = thread(isDaemon = true) {
                    try {
                        repeat(64) {
                            output.write('x'.code)
                            output.flush()
                            Thread.sleep(30)
                        }
                    } catch (_: Exception) {
                        // Expected once the deadline closes the connection.
                    }
                }
                val response = sock.getInputStream().readBytes().toString(Charsets.ISO_8859_1)
                sender.join(1_000)
                assertTrue(response.startsWith("HTTP/1.1 408"))
            }
        } finally {
            shim.stop()
        }
    }

    private fun writeOk(output: java.io.OutputStream) {
        val body = "ok".toByteArray()
        output.write(
            "HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: ${body.size}\r\nConnection: close\r\n\r\n".toByteArray(),
        )
        output.write(body)
        output.flush()
    }

    private fun hasSetCookie(res: String): Boolean =
        res.lineSequence().any { it.startsWith("Set-Cookie:", ignoreCase = true) }

    private fun exchange(port: Int, request: String): String {
        Socket("127.0.0.1", port).use { sock ->
            sock.soTimeout = 3_000
            sock.getOutputStream().write(request.toByteArray(Charsets.ISO_8859_1))
            sock.getOutputStream().flush()
            return sock.getInputStream().readBytes().toString(Charsets.ISO_8859_1)
        }
    }
}
