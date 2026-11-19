package com.remedy.groveconnect.api

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

class PartnerModelsTest {
    private val headJson = """
        {"ok":true,
         "head":{"session_id":"h1","state":"working"},
         "capacity_note":"This PC can run 2 jobs at once.",
         "children":[
           {"child_id":"c1","session_id":"s1","label":"Find flights","task":"t","state":"working","last_report":"Looking"},
           {"child_id":"c2","session_id":"s2","label":"","task":"Clean downloads folder","state":"done"},
           {"child_id":"c3","session_id":"s3","label":"Pay bill","task":"t","state":"waiting"},
           {"child_id":"","label":"ghost"}
         ],
         "goals":{"g1":{"goal_id":"g1","autopilot":true,"state":"working","last_update":"Booked a call"}},
         "routines":[{"routine_id":"r1","title":"Morning news","what":"w","when":"every day at 08:00","enabled":false,"next_run":""}],
         "queue":[{"kind":"child"},{"kind":"goal"}],
         "activity":[{"id":"a1","ts":"2026-11-19T10:00:00Z","kind":"child","title":"Finished: Clean downloads"}, {"id":"a2","title":""}],
         "activity_unseen":3,
         "heartbeat":{"enabled":true,"last_note":"All quiet"}}
    """.trimIndent()

    @Test
    fun parsesHeadOverview() {
        val h = HeadOverview.parse(JSONObject(headJson))
        assertEquals("h1", h.headSessionId)
        assertEquals("working", h.headState)
        assertEquals(3, h.children.size) // blank ids dropped
        assertEquals("Clean downloads folder", h.children[1].label) // label falls back to task
        assertEquals(listOf("c1", "c3"), h.activeChildren.map { it.id })
        assertEquals(2, h.queued)
        assertEquals(1, h.activity.size) // untitled rows dropped
        assertEquals(3, h.activityUnseen)
        assertTrue(h.heartbeatOn)
        assertEquals("All quiet", h.heartbeatNote)
        assertFalse(h.routines.single().enabled)
        assertNull(h.routines.single().nextRun)
    }

    @Test
    fun parsesEmptyAndBrokenHeadWithoutThrowing() {
        val h = HeadOverview.parse(JSONObject("{}"))
        assertNull(h.headSessionId)
        assertEquals("idle", h.headState)
        assertTrue(h.children.isEmpty())
        assertTrue(h.activeChildren.isEmpty())
    }

    @Test
    fun goalsJoinAutopilotLinksAndPutDoneLast() {
        val h = HeadOverview.parse(JSONObject(headJson))
        val goals = parseGoals(
            JSONObject(
                """{"goals":[
                  {"id":"g0","title":"Old","status":"done"},
                  {"id":"g1","title":"New job","status":"active","next_action":"Polish resume"},
                  {"id":"","title":"no id"}]}""",
            ),
            h.goalLinks,
        )
        assertEquals(listOf("g1", "g0"), goals.map { it.id })
        assertTrue(goals[0].autopilot)
        assertEquals("working", goals[0].state)
        assertEquals("Booked a call", goals[0].lastUpdate)
        assertFalse(goals[1].autopilot)
        assertTrue(parseGoals(null, emptyMap()).isEmpty())
    }

    @Test
    fun decisionsOldestFirstPendingOnlyNoLifeDrive() {
        val json = JSONObject(
            """{"approvals":[
              {"id":"b","tool_name":"bash_exec","summary":"Run ls","created_at":20,"status":"pending"},
              {"id":"a","tool_name":"mail_send","summary":"Send mail","sensitive":true,"created_at":10},
              {"id":"c","tool_name":"life_drive","summary":"plan","created_at":5},
              {"id":"d","tool_name":"bash_exec","created_at":1,"status":"approved"}]}""",
        )
        val d = parseDecisions(json)
        assertEquals(listOf("a", "b"), d.map { it.id })
        assertTrue(d[0].sensitive)
        assertTrue(parseDecisions(null).isEmpty())
    }

    @Test
    fun phoneAlwaysAnswersOnceIncludingSensitive() {
        val ordinary = Decision.parse(JSONObject("""{"id":"x","tool_name":"bash_exec"}"""))
        val sensitive = Decision.parse(JSONObject("""{"id":"y","tool_name":"payment","sensitive":true}"""))
        for (d in listOf(ordinary, sensitive)) {
            assertEquals("once", resolveScope(d, true))
            assertEquals("once", resolveScope(d, false))
        }
    }

    @Test
    fun headlineFallsBackToReasonThenPlainWords() {
        assertEquals("Run it", decisionHeadline(Decision.parse(JSONObject("""{"id":"1","summary":" Run it "}"""))))
        assertEquals("Because", decisionHeadline(Decision.parse(JSONObject("""{"id":"1","reason":"Because"}"""))))
        assertEquals("Remedy needs your OK to continue.", decisionHeadline(Decision.parse(JSONObject("""{"id":"1"}"""))))
    }

    @Test
    fun explainDropsRuntimeFieldsAndKeepsRawText() {
        val rows = explainCommand("""{"to":"a@b.c","subject":"Hi","session_id":"s","caller_origin":"phone","body":""}""")
        // Android's org.json keeps key order; the JVM test copy does not.
        assertEquals(setOf("To" to "a@b.c", "Subject" to "Hi"), rows.toSet())
        assertEquals(2, rows.size)
        assertEquals(listOf("" to "ls -la"), explainCommand("ls -la"))
        assertEquals(listOf("" to "{broken"), explainCommand("{broken"))
        assertEquals(listOf("" to """{"session_id":"s"}"""), explainCommand("""{"session_id":"s"}"""))
        assertTrue(explainCommand("  ").isEmpty())
    }

    @Test
    fun genericReasons() {
        assertTrue(isGenericReason(null))
        assertTrue(isGenericReason(" Tool requires your approval "))
        assertTrue(isGenericReason("Approval required"))
        assertFalse(isGenericReason("Sends money to Alex"))
    }

    @Test
    fun stateWordsAndFlags() {
        assertEquals("Working", stateWords("working"))
        assertEquals("Waiting for room", stateWords("queued"))
        assertEquals("Needs you", stateWords("waiting"))
        assertEquals("Needs you", stateWords("blocked"))
        assertEquals("Hit a problem", stateWords("error"))
        assertEquals("Resting", stateWords(null))
        assertEquals("Resting", stateWords("something-new"))
        assertTrue(needsOwner("error"))
        assertFalse(needsOwner("working"))
        assertTrue(canStop("queued"))
        assertFalse(canStop("done"))
        assertFalse(canStop("stopped"))
    }

    @Test
    fun relativeWhenBothDirections() {
        val now = java.time.Instant.parse("2026-11-19T12:00:00Z").toEpochMilli()
        assertEquals("in 3h", relativeWhen("2026-11-19T15:00:00Z", now))
        assertEquals("12m ago", relativeWhen("2026-11-19T11:48:00Z", now))
        assertEquals("2d ago", relativeWhen("2026-11-17T12:00:00+00:00", now))
        assertEquals("just now", relativeWhen("2026-11-19T11:59:50Z", now))
        assertEquals("now", relativeWhen("2026-11-19T12:00:10Z", now))
        assertEquals("", relativeWhen("not a date", now))
        assertEquals("", relativeWhen(null, now))
    }

    @Test
    fun headSummaryInPlainWords() {
        val h = HeadOverview.parse(JSONObject(headJson))
        assertEquals("Remedy is working · 2 side jobs · 1 question for you", headSummary(h, 1))
        assertEquals("Checking in with Remedy…", headSummary(null, 0))
        val idle = HeadOverview.parse(JSONObject("""{"head":{"state":"idle"}}"""))
        assertEquals("Remedy is here · 3 questions for you", headSummary(idle, 3))
    }
}
