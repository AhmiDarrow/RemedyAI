package com.remedy.groveconnect.api

import org.json.JSONObject
import java.util.concurrent.atomic.AtomicBoolean

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
        val completed = AtomicBoolean(false)
        val finish = { if (completed.compareAndSet(false, true)) onDone() }
        return api.streamSsePost(
            "/api/sessions/$sessionId/messages/stream",
            body,
            onEvent = { event, payload ->
                when (event) {
                    "token" -> payload.optString("text").takeIf { it.isNotEmpty() }?.let(onToken)
                    "thinking" -> payload.optString("text").takeIf { it.isNotEmpty() }?.let(onThinking)
                    "tool_call" -> payload.optString("name").takeIf { it.isNotEmpty() }?.let(onTool)
                    "tool_result" -> payload.optString("name").takeIf { it.isNotEmpty() }?.let { onTool("$it ✓") }
                    "aborted", "done", "exit" -> finish()
                }
            },
            onDone = finish,
            onError = onError,
        )
    }

    /** Abort the running turn in a session. */
    fun stop(sessionId: String?): Boolean {
        if (sessionId.isNullOrBlank()) return false
        return api.post("/api/sessions/$sessionId/abort")
    }

    /** The PC's active session (streaming turn first, else focused tab). */
    fun activeSessionId(): String? =
        api.getJson("/connect/me")?.optString("session_id")?.takeIf { it.isNotBlank() }

    /** Live provider + model from the PC (GET /api/settings). */
    fun currentSettings(): Pair<String?, String?> {
        val j = api.getJson("/api/settings") ?: return null to null
        return j.optString("llm_provider").takeIf { it.isNotBlank() } to
            j.optString("llm_model").takeIf { it.isNotBlank() }
    }

    /** Switch the PC's active provider / model (safe keys only, no secrets). */
    fun setProvider(provider: String?, model: String?): Boolean {
        val body = JSONObject()
        if (!provider.isNullOrBlank()) body.put("llm_provider", provider)
        if (!model.isNullOrBlank()) body.put("llm_model", model)
        if (body.length() == 0) return false
        return api.putJson("/api/settings", body) != null
    }

    /**
     * Reset the PC's model to the provider default. The server merges patches
     * and drops nulls, so an explicit empty `llm_model` is the only way to say
     * "clear it" — normalize_llm_settings then picks the provider default.
     */
    fun resetModel(provider: String?): Boolean {
        val body = JSONObject().put("llm_model", "")
        if (!provider.isNullOrBlank()) body.put("llm_provider", provider)
        return api.putJson("/api/settings", body) != null
    }

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
