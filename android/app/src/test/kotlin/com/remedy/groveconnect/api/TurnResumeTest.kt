package com.remedy.groveconnect.api

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class TurnResumeTest {
    @Test
    fun clientErrorsReloadEverythingElseRetries() {
        // The family of "the turn is over / never ran" answers from the PC.
        for (e in listOf("HTTP 400", "HTTP 404", "HTTP 403", "HTTP 409", "HTTP 400 request_id required")) {
            assertEquals(e, ResumeAction.Reload, resumeActionFor(e))
        }
        // The family of "the link dropped": transport errors, server errors, blanks.
        for (e in listOf(
            "HTTP 500", "HTTP 502", "Connection reset", "timeout", "stream error",
            "unexpected end of stream on HTTP 400 proxy", "", null,
        )) {
            assertEquals(e.toString(), ResumeAction.Retry, resumeActionFor(e))
        }
    }

    @Test
    fun backoffGrowsAndCaps() {
        assertEquals(2_000L, resumeDelayMs(0))
        assertEquals(4_000L, resumeDelayMs(1))
        assertEquals(16_000L, resumeDelayMs(3))
        assertEquals(20_000L, resumeDelayMs(4))
        assertEquals(20_000L, resumeDelayMs(50))
        assertEquals(2_000L, resumeDelayMs(-1))
    }

    @Test
    fun turnMarkTracksRequestAndHighestSeq() {
        val m = TurnMark()
        assertTrue(m.observe(JSONObject("""{"type":"start","request_id":"r1","seq":1}""")))
        assertTrue(m.observe(JSONObject("""{"type":"token","text":"hi","seq":5}""")))
        assertFalse(m.observe(JSONObject("""{"type":"token","text":"old","seq":3}""")))
        assertFalse(m.observe(JSONObject("""{"type":"token","text":"no seq"}""")))
        assertEquals("r1", m.requestId)
        assertEquals(5L, m.seq)
    }

    @Test
    fun attachPathCarriesRequestAndAfter() {
        assertEquals("/api/sessions/s1/stream/attach", attachPath("s1", null, 0))
        assertEquals("/api/sessions/s1/stream/attach?request_id=r%2F1&after=7", attachPath("s1", "r/1", 7))
        assertEquals("/api/sessions/s1/stream/attach?after=3", attachPath("s1", " ", 3))
    }

    @Test
    fun plainProblemsForTheOwner() {
        assertEquals("Couldn't reach your PC (refused).", plainProblem(0, null, "refused"))
        assertTrue(plainProblem(403, "pane:live_ui").startsWith("Your PC doesn't allow that from the phone."))
        assertEquals("Child not found", plainProblem(404, "child not found"))
        assertEquals("That's no longer there.", plainProblem(404, null))
        assertEquals("Remedy is busy with that right now.", plainProblem(409, " "))
        assertEquals("That didn't go through (HTTP 500).", plainProblem(500, null))
    }
}
