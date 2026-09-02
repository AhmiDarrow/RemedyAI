package com.remedy.groveconnect.api

import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

/**
 * Terminal API — talks to the /api/terminal SSE route the phone gets over the
 * same encrypted tunnel as everything else. The server keeps the shell alive
 * for a grace period after a dropped stream, so rotation/reconnect is safe.
 */
class TerminalApi(private val api: RemedyApi) {

    /** Open a shell; returns the terminal id or null. */
    fun open(cwd: String? = null, cols: Int = 100, rows: Int = 28): String? {
        val body = JSONObject()
            .put("cwd", cwd ?: JSONObject.NULL)
            .put("cols", cols)
            .put("rows", rows)
        return api.postJson("/api/terminal", body)?.optString("terminal_id")?.takeIf { it.isNotBlank() }
    }

    /**
     * Stream terminal output. [onOutput] receives decoded text chunks; [onExit]
     * fires with the process exit code (null on error); [onError] with a message.
     */
    fun stream(
        terminalId: String,
        onOutput: (String) -> Unit,
        onExit: (Int?) -> Unit,
        onError: (String) -> Unit,
    ): () -> Unit {
        val completed = AtomicBoolean(false)
        val finish = { code: Int? -> if (completed.compareAndSet(false, true)) onExit(code) }
        return api.streamSse(
            "/api/terminal/$terminalId/stream",
            onEvent = { event, payload ->
                when (event) {
                    "output" -> {
                        val text = payload.optString("text")
                        if (text.isNotEmpty()) onOutput(text)
                    }
                    "exit" -> finish(payload.optInt("code", -1).let { if (it < 0) null else it })
                }
            },
            onDone = { finish(null) },
            onError = onError,
        )
    }

    /** Write text to the shell's stdin. */
    fun input(terminalId: String, data: String): Boolean =
        api.postJson("/api/terminal/$terminalId/input", JSONObject().put("data", data)) != null

    /** Resize the terminal. */
    fun resize(terminalId: String, cols: Int, rows: Int): Boolean =
        api.postJson("/api/terminal/$terminalId/resize", JSONObject().put("cols", cols).put("rows", rows)) != null

    /** Close the shell. */
    fun close(terminalId: String): Boolean = api.delete("/api/terminal/$terminalId")
}
