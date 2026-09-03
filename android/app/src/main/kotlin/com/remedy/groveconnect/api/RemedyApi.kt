package com.remedy.groveconnect.api

import org.json.JSONArray
import org.json.JSONObject
import java.io.Closeable
import java.io.IOException
import java.io.InputStreamReader
import java.io.OutputStream
import java.io.Reader
import java.net.HttpURLConnection
import java.net.URL
import java.util.concurrent.ExecutorService
import java.util.concurrent.Executors
import java.util.concurrent.atomic.AtomicBoolean
import java.util.concurrent.atomic.AtomicReference

/**
 * Buffered line reader with a hard per-line allocation bound.
 *
 * java.io.BufferedReader.readLine() grows until a newline appears, so checking
 * the returned String is too late for a malicious or damaged SSE peer.
 */
internal class BoundedLineReader(
    private val source: Reader,
    private val maxChars: Int,
) : Closeable {
    private val buffer = CharArray(8192)
    private var start = 0
    private var end = 0

    init {
        require(maxChars > 0) { "maxChars must be positive" }
    }

    fun readLine(): String? {
        val line = StringBuilder(minOf(256, maxChars))
        var sawAny = false
        while (true) {
            if (start >= end) {
                end = source.read(buffer)
                start = 0
                if (end < 0) return if (sawAny) finish(line) else null
                if (end == 0) continue
            }
            var newline = start
            while (newline < end && buffer[newline] != '\n') newline++
            val count = newline - start
            if (line.length + count > maxChars) {
                throw IOException("stream line exceeds $maxChars characters")
            }
            if (count > 0) {
                line.append(buffer, start, count)
                sawAny = true
            }
            start = newline
            if (start < end && buffer[start] == '\n') {
                start++
                return finish(line)
            }
        }
    }

    private fun finish(line: StringBuilder): String {
        if (line.isNotEmpty() && line[line.length - 1] == '\r') {
            line.setLength(line.length - 1)
        }
        return line.toString()
    }

    override fun close() = source.close()
}

/**
 * Minimal HTTP client that talks to the PC's API through the loopback shim.
 *
 * The shim URL is `http://127.0.0.1:{port}/{token}/` — the token path is the
 * auth. Every request carries it, so no cookies or headers are needed.
 * JSON endpoints return parsed objects; SSE endpoints stream `event:`/`data:`
 * frames to a callback on a background thread.
 */
class RemedyApi(private val baseUrl: String) {
    private val io: ExecutorService = Executors.newFixedThreadPool(6) { r ->
        Thread(r, "grove-api").apply { isDaemon = true }
    }
    // SSE callbacks must land on the main thread: the UI mutates Compose
    // snapshot state (messages, lines, flags) from these callbacks, and
    // mutating snapshot state off the main thread throws
    // IllegalStateException — the "unhandled error exception" crashes.
    private val main = android.os.Handler(android.os.Looper.getMainLooper())

    fun shutdown() {
        io.shutdownNow()
    }

    private fun conn(path: String, method: String): HttpURLConnection {
        val url = URL(baseUrl.trimEnd('/') + path)
        val c = url.openConnection() as HttpURLConnection
        c.requestMethod = method
        // Mobile data rides the relay/tunnel: 8s connect / 15s read is too
        // tight for a settings/model switch (server-side model discovery is a
        // network call). Generous timeouts; SSE streams get their own below.
        c.connectTimeout = 15_000
        c.readTimeout = 45_000
        c.setRequestProperty("Accept", "application/json")
        c.setRequestProperty("Connection", "keep-alive")
        return c
    }

    private fun readBody(c: HttpURLConnection): String {
        val stream = if (c.responseCode in 200..299) c.inputStream else c.errorStream
            ?: return ""
        return stream.bufferedReader(Charsets.UTF_8).use { it.readText() }
    }

    /** GET returning a parsed JSON object, or null on non-2xx. */
    fun getJson(path: String): JSONObject? {
        val c = conn(path, "GET")
        return try {
            if (c.responseCode !in 200..299) null
            else JSONObject(readBody(c))
        } catch (e: android.os.NetworkOnMainThreadException) {
            // Never swallow this: a Main-thread call is a programming error
            // that must crash loudly instead of silently returning null.
            throw e
        } catch (_: Exception) {
            null
        } finally {
            c.disconnect()
        }
    }

    /** GET returning a parsed JSON array, or null on non-2xx. */
    fun getJsonArray(path: String): JSONArray? {
        val c = conn(path, "GET")
        return try {
            if (c.responseCode !in 200..299) null
            else JSONArray(readBody(c))
        } catch (e: android.os.NetworkOnMainThreadException) {
            // Never swallow this: a Main-thread call is a programming error
            // that must crash loudly instead of silently returning null.
            throw e
        } catch (_: Exception) {
            null
        } finally {
            c.disconnect()
        }
    }

    /** POST JSON body; returns parsed response object or null on non-2xx. */
    fun postJson(path: String, body: JSONObject?): JSONObject? {
        val c = conn(path, "POST")
        return try {
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                val bytes = body.toString().toByteArray(Charsets.UTF_8)
                c.setFixedLengthStreamingMode(bytes.size)
                (c.outputStream as OutputStream).use { it.write(bytes) }
            }
            if (c.responseCode !in 200..299) null
            else JSONObject(readBody(c))
        } catch (e: android.os.NetworkOnMainThreadException) {
            // Never swallow this: a Main-thread call is a programming error
            // that must crash loudly instead of silently returning null.
            throw e
        } catch (_: Exception) {
            null
        } finally {
            c.disconnect()
        }
    }

    /** POST with no body (or raw text); returns true on 2xx. */
    fun post(path: String, rawBody: String? = null): Boolean {
        val c = conn(path, "POST")
        return try {
            if (rawBody != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                val bytes = rawBody.toByteArray(Charsets.UTF_8)
                c.setFixedLengthStreamingMode(bytes.size)
                (c.outputStream as OutputStream).use { it.write(bytes) }
            }
            c.responseCode in 200..299
        } catch (e: android.os.NetworkOnMainThreadException) {
            // Never swallow this: a Main-thread call is a programming error
            // that must crash loudly instead of silently returning null.
            throw e
        } catch (_: Exception) {
            false
        } finally {
            c.disconnect()
        }
    }

    /** PUT JSON body; returns parsed response object or null on non-2xx. */
    fun putJson(path: String, body: JSONObject?): JSONObject? {
        val c = conn(path, "PUT")
        return try {
            if (body != null) {
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                val bytes = body.toString().toByteArray(Charsets.UTF_8)
                c.setFixedLengthStreamingMode(bytes.size)
                (c.outputStream as OutputStream).use { it.write(bytes) }
            }
            if (c.responseCode !in 200..299) null
            else JSONObject(readBody(c))
        } catch (e: android.os.NetworkOnMainThreadException) {
            // Never swallow this: a Main-thread call is a programming error
            // that must crash loudly instead of silently returning null.
            throw e
        } catch (_: Exception) {
            null
        } finally {
            c.disconnect()
        }
    }

    /** DELETE; returns true on 2xx. */
    fun delete(path: String): Boolean {
        val c = conn(path, "DELETE")
        return try {
            c.responseCode in 200..299
        } catch (e: android.os.NetworkOnMainThreadException) {
            // Never swallow this: a Main-thread call is a programming error
            // that must crash loudly instead of silently returning null.
            throw e
        } catch (_: Exception) {
            false
        } finally {
            c.disconnect()
        }
    }

    /**
     * Stream an SSE endpoint. Calls [onEvent] with (eventName, jsonData) per
     * frame on the MAIN thread (safe for Compose state); [onDone] when the
     * stream closes; [onError] with a message on failure. Returns a cancel handle.
     */
    fun streamSse(
        path: String,
        onEvent: (String, JSONObject) -> Unit,
        onDone: () -> Unit,
        onError: (String) -> Unit,
    ): () -> Unit {
        val cancelled = AtomicBoolean(false)
        val active = AtomicReference<HttpURLConnection?>(null)
        val fut = io.submit {
            var c: HttpURLConnection? = null
            try {
                c = conn(path, "GET")
                active.set(c)
                c.setRequestProperty("Accept", "text/event-stream")
                c.setRequestProperty("Cache-Control", "no-cache")
                if (c.responseCode !in 200..299) {
                    val code = c.responseCode
                    main.post { if (!cancelled.get()) onError("HTTP $code") }
                    return@submit
                }
                BoundedLineReader(
                    InputStreamReader(c.inputStream, Charsets.UTF_8),
                    MAX_SSE_LINE_CHARS,
                ).use { reader ->
                    var event = ""
                    val data = StringBuilder()
                    while (!cancelled.get()) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) {
                            if (data.isNotEmpty()) {
                                val payload = try {
                                    JSONObject(data.toString())
                                } catch (_: Exception) {
                                    JSONObject().put("text", data.toString())
                                }
                                val ev = event.ifEmpty { "message" }
                                event = ""
                                data.setLength(0)
                                main.post { if (!cancelled.get()) onEvent(ev, payload) }
                            } else {
                                event = ""
                                data.setLength(0)
                            }
                            continue
                        }
                        when {
                            line.startsWith("event:") -> event = line.substringAfter(':').trim()
                            line.startsWith("data:") -> {
                                if (data.isNotEmpty()) data.append('\n')
                                data.append(line.substringAfter(':').trim())
                                if (data.length > MAX_SSE_EVENT_CHARS) {
                                    throw IOException("stream event exceeds 1 MiB")
                                }
                            }
                            line.startsWith(":") -> Unit // comment / keepalive
                        }
                    }
                }
                main.post { if (!cancelled.get()) onDone() }
            } catch (e: Exception) {
                val msg = e.message ?: "stream error"
                main.post { if (!cancelled.get()) onError(msg) }
            } finally {
                c?.let {
                    active.compareAndSet(it, null)
                    it.disconnect()
                }
            }
        }
        return {
            cancelled.set(true)
            active.getAndSet(null)?.disconnect()
            fut.cancel(true)
        }
    }

    /**
     * Stream an SSE endpoint that needs a POST + JSON body (chat turns).
     * Same frame protocol as [streamSse]; the server streams `event:`/`data:`
     * frames while the request body carries the message.
     */
    fun streamSsePost(
        path: String,
        body: JSONObject,
        onEvent: (String, JSONObject) -> Unit,
        onDone: () -> Unit,
        onError: (String) -> Unit,
    ): () -> Unit {
        val cancelled = AtomicBoolean(false)
        val active = AtomicReference<HttpURLConnection?>(null)
        val fut = io.submit {
            var c: HttpURLConnection? = null
            try {
                c = conn(path, "POST")
                active.set(c)
                c.setRequestProperty("Accept", "text/event-stream")
                c.setRequestProperty("Cache-Control", "no-cache")
                c.doOutput = true
                c.setRequestProperty("Content-Type", "application/json")
                val bytes = body.toString().toByteArray(Charsets.UTF_8)
                c.setFixedLengthStreamingMode(bytes.size)
                (c.outputStream as OutputStream).use { it.write(bytes) }
                if (c.responseCode !in 200..299) {
                    val code = c.responseCode
                    main.post { if (!cancelled.get()) onError("HTTP $code") }
                    return@submit
                }
                BoundedLineReader(
                    InputStreamReader(c.inputStream, Charsets.UTF_8),
                    MAX_SSE_LINE_CHARS,
                ).use { reader ->
                    var event = ""
                    val data = StringBuilder()
                    while (!cancelled.get()) {
                        val line = reader.readLine() ?: break
                        if (line.isEmpty()) {
                            if (data.isNotEmpty()) {
                                val payload = try {
                                    JSONObject(data.toString())
                                } catch (_: Exception) {
                                    JSONObject().put("text", data.toString())
                                }
                                val ev = event.ifEmpty { "message" }
                                event = ""
                                data.setLength(0)
                                main.post { if (!cancelled.get()) onEvent(ev, payload) }
                            } else {
                                event = ""
                                data.setLength(0)
                            }
                            continue
                        }
                        when {
                            line.startsWith("event:") -> event = line.substringAfter(':').trim()
                            line.startsWith("data:") -> {
                                if (data.isNotEmpty()) data.append('\n')
                                data.append(line.substringAfter(':').trim())
                                if (data.length > MAX_SSE_EVENT_CHARS) {
                                    throw IOException("stream event exceeds 1 MiB")
                                }
                            }
                            line.startsWith(":") -> Unit // comment / keepalive
                        }
                    }
                }
                main.post { if (!cancelled.get()) onDone() }
            } catch (e: Exception) {
                val msg = e.message ?: "stream error"
                main.post { if (!cancelled.get()) onError(msg) }
            } finally {
                c?.let {
                    active.compareAndSet(it, null)
                    it.disconnect()
                }
            }
        }
        return {
            cancelled.set(true)
            active.getAndSet(null)?.disconnect()
            fut.cancel(true)
        }
    }

    private companion object {
        const val MAX_SSE_EVENT_CHARS = 1024 * 1024
        const val MAX_SSE_LINE_CHARS = MAX_SSE_EVENT_CHARS + 16
    }
}
