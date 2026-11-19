package com.remedy.groveconnect.api

import org.json.JSONObject

/**
 * Remedy's head over the tunnel: overview, side jobs, goals, routines and
 * decisions. Every call is blocking — run it off the main thread.
 *
 * The phone is an untrusted origin on the PC. Nothing here bypasses that:
 * the runtime decides what asks, and payment / password / send checkpoints
 * only ever take a one-time yes.
 */
class PartnerApi(private val api: RemedyApi) {

    fun head(): HeadOverview? = api.getJson("/api/partner/head")?.let(HeadOverview::parse)

    fun goals(links: Map<String, JSONObject>): List<GoalRow> = parseGoals(api.getJson("/api/goals"), links)

    /** Remedy's own conversation (creates it when she has none yet). */
    fun headSessionId(): String? =
        api.send("POST", "/api/partner/head/session", JSONObject()).json
            ?.optString("session_id")?.takeIf { it.isNotBlank() }

    fun stopChild(id: String): ApiResult = api.send("POST", "/api/partner/children/${enc(id)}/stop", JSONObject())

    fun startChild(task: String): ApiResult =
        api.send("POST", "/api/partner/children", JSONObject().put("task", task))

    fun setAutopilot(goalId: String, on: Boolean): ApiResult =
        api.send("PUT", "/api/partner/goals/${enc(goalId)}", JSONObject().put("autopilot", on))

    fun stepGoal(goalId: String): ApiResult =
        api.send("POST", "/api/partner/goals/${enc(goalId)}/step", JSONObject())

    fun setRoutineEnabled(id: String, on: Boolean): ApiResult =
        api.send("PATCH", "/api/partner/routines/${enc(id)}", JSONObject().put("enabled", on))

    fun runRoutine(id: String): ApiResult =
        api.send("POST", "/api/partner/routines/${enc(id)}/run", JSONObject())

    fun markSeen(): ApiResult = api.send("POST", "/api/partner/activity/seen", JSONObject())

    fun decisions(): List<Decision> = parseDecisions(api.getJson("/api/approvals"))

    /**
     * Answer one decision. The phone only ever says yes for this one action
     * ([resolveScope]) — standing "allow for this chat" stays on the PC.
     */
    fun answer(d: Decision, approve: Boolean): ApiResult =
        api.send(
            "POST",
            "/api/approvals/${enc(d.id)}/resolve",
            JSONObject().put("approve", approve).put("scope", resolveScope(d, approve)),
        )

    private fun enc(s: String): String = java.net.URLEncoder.encode(s, "UTF-8").replace("+", "%20")
}

/**
 * The phone always answers "once": a yes covers this single action and the
 * next identical one asks again. Sensitive checkpoints are one-shot on the
 * PC regardless; this keeps ordinary asks the same from a phone.
 */
@Suppress("UNUSED_PARAMETER")
fun resolveScope(d: Decision, approve: Boolean): String = "once"
