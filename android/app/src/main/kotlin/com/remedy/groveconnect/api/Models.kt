package com.remedy.groveconnect.api

import org.json.JSONArray
import org.json.JSONObject

/** A chat session as returned by GET /api/sessions. */
data class ChatSession(
    val id: String,
    val title: String,
    val model: String?,
    val agent: String?,
    val messageCount: Int,
    val updatedAt: Long?,
    val createdAt: Long?,
) {
    val displayTitle: String
        get() = title.ifBlank { "New session" }

    companion object {
        fun fromJson(o: JSONObject): ChatSession {
            val updated = o.optString("updated_at").ifBlank { o.optString("created_at") }
            return ChatSession(
                id = o.optString("id"),
                title = o.optString("title"),
                model = o.optString("model").ifBlank { null },
                agent = o.optString("agent").ifBlank { null },
                messageCount = o.optInt("message_count"),
                updatedAt = parseIso(updated),
                createdAt = parseIso(o.optString("created_at")),
            )
        }

        private fun parseIso(s: String): Long? =
            try {
                java.time.OffsetDateTime.parse(s).toInstant().toEpochMilli()
            } catch (_: Exception) {
                null
            }
    }
}

/** One chat message from GET /api/sessions/{id}/messages. */
data class ChatMessage(
    val id: String,
    val role: String, // user | assistant
    val content: String,
    val thinking: String?,
    val model: String?,
    val createdAt: Long?,
) {
    val isUser: Boolean get() = role == "user"
    val isAssistant: Boolean get() = role == "assistant"

    companion object {
        fun fromJson(o: JSONObject): ChatMessage {
            val created = o.optString("created_at")
            return ChatMessage(
                id = o.optString("id"),
                role = o.optString("role"),
                content = o.optString("content"),
                thinking = o.optString("thinking").ifBlank { null },
                model = o.optString("model").ifBlank { null },
                createdAt = try {
                    java.time.OffsetDateTime.parse(created).toInstant().toEpochMilli()
                } catch (_: Exception) {
                    null
                },
            )
        }
    }
}

fun parseSessions(json: JSONObject): List<ChatSession> {
    val arr = json.optJSONArray("sessions") ?: JSONArray()
    val out = ArrayList<ChatSession>(arr.length())
    for (i in 0 until arr.length()) {
        out += ChatSession.fromJson(arr.getJSONObject(i))
    }
    return out
}

fun parseMessages(json: JSONObject): List<ChatMessage> {
    val arr = json.optJSONArray("messages") ?: JSONArray()
    val out = ArrayList<ChatMessage>(arr.length())
    for (i in 0 until arr.length()) {
        out += ChatMessage.fromJson(arr.getJSONObject(i))
    }
    return out
}
