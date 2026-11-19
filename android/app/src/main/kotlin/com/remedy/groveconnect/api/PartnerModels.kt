package com.remedy.groveconnect.api

import org.json.JSONArray
import org.json.JSONObject

/*
 * Remedy's head as the phone sees it: what she is doing, her side jobs
 * ("children"), goals, routines and the "while you were away" timeline.
 * Shapes follow GET /api/partner/head (native/go/httpapi/partner_api.go).
 *
 * Everything here is pure (no Android types) so it runs in JVM unit tests.
 */

/** A side job Remedy started. */
data class HeadChild(
    val id: String,
    val sessionId: String,
    val label: String,
    val task: String,
    val state: String,
    val lastReport: String?,
    val updatedAt: String?,
)

/** A goal from GET /api/goals joined with its autopilot link from the head. */
data class GoalRow(
    val id: String,
    val title: String,
    val status: String,
    val nextAction: String?,
    val autopilot: Boolean,
    val state: String,
    val lastUpdate: String?,
)

data class HeadRoutine(
    val id: String,
    val title: String,
    val whenText: String,
    val enabled: Boolean,
    val nextRun: String?,
    val lastState: String?,
    val lastSummary: String?,
)

data class HeadActivity(
    val id: String,
    val ts: String,
    val kind: String,
    val title: String,
    val detail: String?,
    val state: String?,
)

data class HeadOverview(
    val headSessionId: String?,
    val headState: String,
    val capacityNote: String,
    val children: List<HeadChild>,
    val goalLinks: Map<String, JSONObject>,
    val routines: List<HeadRoutine>,
    val queued: Int,
    val activity: List<HeadActivity>,
    val activityUnseen: Int,
    val heartbeatOn: Boolean,
    val heartbeatNote: String?,
) {
    /** Side jobs still going (working, queued or waiting on the owner). */
    val activeChildren: List<HeadChild>
        get() = children.filter { it.state in ACTIVE_STATES }

    companion object {
        private val ACTIVE_STATES = setOf("working", "queued", "waiting", "blocked")

        fun parse(o: JSONObject): HeadOverview {
            val head = o.optJSONObject("head")
            val children = objects(o.optJSONArray("children")).map { c ->
                HeadChild(
                    id = c.optString("child_id"),
                    sessionId = c.optString("session_id"),
                    label = c.optString("label").ifBlank { c.optString("task").take(60) }.ifBlank { "Side job" },
                    task = c.optString("task"),
                    state = c.optString("state").ifBlank { "idle" },
                    lastReport = c.optString("last_report").ifBlank { null },
                    updatedAt = c.optString("updated_at").ifBlank { null },
                )
            }.filter { it.id.isNotBlank() }
            val links = LinkedHashMap<String, JSONObject>()
            o.optJSONObject("goals")?.let { g ->
                for (k in g.keys()) g.optJSONObject(k)?.let { links[k] = it }
            }
            val routines = objects(o.optJSONArray("routines")).map { r ->
                HeadRoutine(
                    id = r.optString("routine_id"),
                    title = r.optString("title").ifBlank { r.optString("what").take(60) },
                    whenText = r.optString("when"),
                    enabled = r.optBoolean("enabled", true),
                    nextRun = r.optString("next_run").ifBlank { null },
                    lastState = r.optString("last_state").ifBlank { null },
                    lastSummary = r.optString("last_summary").ifBlank { null },
                )
            }.filter { it.id.isNotBlank() }
            val activity = objects(o.optJSONArray("activity")).map { a ->
                HeadActivity(
                    id = a.optString("id"),
                    ts = a.optString("ts"),
                    kind = a.optString("kind"),
                    title = a.optString("title"),
                    detail = a.optString("detail").ifBlank { null },
                    state = a.optString("state").ifBlank { null },
                )
            }.filter { it.title.isNotBlank() }
            val hb = o.optJSONObject("heartbeat")
            return HeadOverview(
                headSessionId = head?.optString("session_id")?.ifBlank { null },
                headState = head?.optString("state")?.ifBlank { null } ?: "idle",
                capacityNote = o.optString("capacity_note"),
                children = children,
                goalLinks = links,
                routines = routines,
                queued = o.optJSONArray("queue")?.length() ?: 0,
                activity = activity,
                activityUnseen = o.optInt("activity_unseen"),
                heartbeatOn = hb?.optBoolean("enabled") ?: false,
                heartbeatNote = hb?.optString("last_note")?.ifBlank { null },
            )
        }
    }
}

/** Join GET /api/goals rows with the head's autopilot links. Done goals last. */
fun parseGoals(goalsJson: JSONObject?, links: Map<String, JSONObject>): List<GoalRow> {
    val rows = objects(goalsJson?.optJSONArray("goals")).mapNotNull { g ->
        val id = g.optString("id")
        if (id.isBlank()) return@mapNotNull null
        val link = links[id]
        GoalRow(
            id = id,
            title = g.optString("title").ifBlank { id },
            status = g.optString("status").ifBlank { "active" },
            nextAction = g.optString("next_action").ifBlank { null },
            autopilot = link?.optBoolean("autopilot") ?: false,
            state = link?.optString("state")?.ifBlank { null } ?: "idle",
            lastUpdate = link?.optString("last_update")?.ifBlank { null },
        )
    }
    return rows.sortedBy { if (it.status == "done" || it.status == "archived") 1 else 0 }
}

/** One question Remedy is asking the owner (GET /api/approvals row). */
data class Decision(
    val id: String,
    val toolName: String,
    val command: String,
    val reason: String,
    val summary: String,
    val sensitive: Boolean,
    val blocking: Boolean,
    val origin: String?,
    val status: String,
    val createdAt: Double,
) {
    companion object {
        fun parse(o: JSONObject): Decision = Decision(
            id = o.optString("id"),
            toolName = o.optString("tool_name"),
            command = o.optString("command"),
            reason = o.optString("reason"),
            summary = o.optString("summary").ifBlank { o.optString("reason") },
            sensitive = o.optBoolean("sensitive"),
            blocking = o.optBoolean("blocking"),
            origin = o.optString("origin").ifBlank { null },
            status = o.optString("status").ifBlank { "pending" },
            createdAt = o.optDouble("created_at", 0.0).let { if (it.isNaN()) 0.0 else it },
        )
    }
}

fun parseDecisions(json: JSONObject?): List<Decision> =
    orderDecisions(objects(json?.optJSONArray("approvals")).map(Decision::parse))

/**
 * Same order as Grove's decision card: oldest first, pending only, and the
 * internal life-drive rows never surface as cards.
 */
fun orderDecisions(items: List<Decision>): List<Decision> =
    items.filter { it.id.isNotBlank() && it.toolName != "life_drive" && it.status == "pending" }
        .sortedBy { it.createdAt }

/** The card's headline, in plain words. */
fun decisionHeadline(d: Decision): String =
    d.summary.trim().ifBlank { "Remedy needs your OK to continue." }

/** Fields the runtime fills in itself — never shown as part of the ask. */
private val RUNTIME_FIELDS = setOf(
    "caller_origin", "session_id", "home_dir", "workspace_root", "project_path",
    "write_roots", "owner_confirmed", "page_context", "_go_bound",
)

/** Reasons that only restate that a card exists — not worth a line. */
fun isGenericReason(reason: String?): Boolean {
    val r = reason.orEmpty().trim().lowercase()
    return r.isEmpty() || r == "tool requires your approval" || r == "approval required"
}

/**
 * "Explain": exactly what would run, as label/value rows when the command is
 * a JSON object (runtime fields dropped), else the raw text as one row.
 */
fun explainCommand(command: String): List<Pair<String, String>> {
    val raw = command.trim()
    if (raw.isEmpty()) return emptyList()
    if (!raw.startsWith("{")) return listOf("" to raw)
    val obj = try {
        JSONObject(raw)
    } catch (_: Exception) {
        return listOf("" to raw)
    }
    val rows = ArrayList<Pair<String, String>>()
    for (k in obj.keys()) {
        if (k in RUNTIME_FIELDS) continue
        val v = obj.opt(k)
        if (v == null || v == JSONObject.NULL || (v is String && v.isEmpty())) continue
        val label = k.replace('_', ' ').replaceFirstChar { it.uppercase() }
        rows += label to (if (v is String) v else v.toString())
    }
    return rows.ifEmpty { listOf("" to raw) }
}

/** Plain words for a state, the way Grove says it. */
fun stateWords(state: String?): String = when (state) {
    "working" -> "Working"
    "queued" -> "Waiting for room"
    "waiting", "blocked" -> "Needs you"
    "done" -> "Done"
    "stopped" -> "Stopped"
    "error" -> "Hit a problem"
    else -> "Resting"
}

/** A state that wants the owner's eyes. */
fun needsOwner(state: String?): Boolean = state == "waiting" || state == "blocked" || state == "error"

/** A side job that can still be stopped. */
fun canStop(state: String?): Boolean = state == "working" || state == "queued" || state == "waiting" || state == "blocked"

/** "in 3h", "12m ago", "now" relative to [nowMs]. Blank for unparseable input. */
fun relativeWhen(iso: String?, nowMs: Long = System.currentTimeMillis()): String {
    if (iso.isNullOrBlank()) return ""
    val t = try {
        java.time.OffsetDateTime.parse(iso).toInstant().toEpochMilli()
    } catch (_: Exception) {
        try {
            java.time.Instant.parse(iso).toEpochMilli()
        } catch (_: Exception) {
            return ""
        }
    }
    val diff = t - nowMs
    val mins = Math.round(Math.abs(diff) / 60_000.0)
    if (mins < 1) return if (diff >= 0) "now" else "just now"
    val span = when {
        mins < 60 -> "${mins}m"
        mins < 60 * 24 -> "${Math.round(mins / 60.0)}h"
        else -> "${Math.round(mins / (60.0 * 24))}d"
    }
    return if (diff >= 0) "in $span" else "$span ago"
}

/** One short line for the top of the Talk screen. */
fun headSummary(h: HeadOverview?, decisions: Int): String {
    if (h == null) return "Checking in with Remedy…"
    val parts = ArrayList<String>()
    parts += if (h.headState == "working") "Remedy is working" else "Remedy is here"
    val jobs = h.activeChildren.size
    if (jobs == 1) parts += "1 side job" else if (jobs > 1) parts += "$jobs side jobs"
    if (decisions == 1) parts += "1 question for you" else if (decisions > 1) parts += "$decisions questions for you"
    return parts.joinToString(" · ")
}

internal fun objects(arr: JSONArray?): List<JSONObject> {
    if (arr == null) return emptyList()
    val out = ArrayList<JSONObject>(arr.length())
    for (i in 0 until arr.length()) arr.optJSONObject(i)?.let { out += it }
    return out
}
