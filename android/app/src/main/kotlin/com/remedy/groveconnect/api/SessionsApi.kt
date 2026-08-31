package com.remedy.groveconnect.api

import org.json.JSONObject

/**
 * Sessions + approvals API over the tunnel. All calls go through [RemedyApi]
 * (loopback shim → encrypted pipe → PC's localhost API).
 */
class SessionsApi(private val api: RemedyApi) {

    /** List chat sessions (history), newest first. */
    fun listSessions(limit: Int = 100): List<ChatSession> {
        val json = api.getJson("/api/sessions?limit=$limit") ?: return emptyList()
        return parseSessions(json)
    }

    /** Create a new session; returns its id or null. */
    fun createSession(title: String = "New Session", model: String? = null): String? {
        val body = JSONObject().put("title", title)
        if (!model.isNullOrBlank()) body.put("model", model)
        return api.postJson("/api/sessions", body)
            ?.optString("id")?.takeIf { it.isNotBlank() }
    }

    /** Fetch messages for a session (history). */
    fun messages(sessionId: String, limit: Int = 100): List<ChatMessage> {
        val json = api.getJson("/api/sessions/$sessionId/messages?limit=$limit")
            ?: return emptyList()
        return parseMessages(json)
    }

    /**
     * Send a message and stream the turn. Fires onToken/onThinking/onTool as
     * the SSE frames arrive, then onDone. Returns a cancel handle.
     */
    fun sendStream(
        sessionId: String,
        text: String,
        onToken: (String) -> Unit,
        onThinking: (String) -> Unit,
        onTool: (String) -> Unit,
        onDone: () -> Unit,
        onError: (String) -> Unit,
    ): () -> Unit {
        val body = JSONObject().put("message", text)
        return api.streamSsePost(
            "/api/sessions/$sessionId/messages/stream",
            body,
            onEvent = { event, payload ->
                when (event) {
                    "token" -> payload.optString("text").takeIf { it.isNotEmpty() }?.let(onToken)
                    "thinking" -> payload.optString("text").takeIf { it.isNotEmpty() }?.let(onThinking)
                    "tool_call" -> payload.optString("name").takeIf { it.isNotEmpty() }?.let(onTool)
                    "tool_result" -> payload.optString("name").takeIf { it.isNotEmpty() }?.let { onTool("$it ✓") }
                    "aborted", "done", "exit" -> onDone()
                }
            },
            onDone = onDone,
            onError = onError,
        )
    }

    /** Abort the running turn in a session. */
    fun stop(sessionId: String?): Boolean =
        api.post("/api/stop", sessionId?.let { JSONObject().put("session_id", it).toString() })

    /** Pending approvals. */
    fun approvals(): List<Approval> {
        val json = api.getJson("/api/approvals") ?: return emptyList()
        val arr = json.optJSONArray("approvals") ?: return emptyList()
        val out = ArrayList<Approval>(arr.length())
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            out += Approval(
                id = o.optString("id"),
                summary = o.optString("summary").ifBlank { o.optString("reason") },
                sensitive = o.optBoolean("sensitive"),
                sessionId = o.optString("session_id").ifBlank { null },
            )
        }
        return out
    }

    /** Resolve an approval. */
    fun resolveApproval(id: String, approve: Boolean): Boolean =
        api.postJson(
            "/api/approvals/$id/resolve",
            JSONObject().put("approve", approve).put("scope", "session"),
        ) != null
}

data class Approval(
    val id: String,
    val summary: String,
    val sensitive: Boolean,
    val sessionId: String?,
)
