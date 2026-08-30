package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ConnectMeTest {
    @Test
    fun parseJsonReadsSessionId() {
        val me = ConnectMe.parseJson(
            """{"session_id":"sid-live-turn","device_id":"dev-1","reachable":"lan","panes":{"chat":true}}""",
        )
        assertEquals("sid-live-turn", me.sessionId)
        assertEquals("dev-1", me.deviceId)
        assertEquals("lan", me.reachable)
    }

    @Test
    fun parseJsonAllowsMissingSessionId() {
        val me = ConnectMe.parseJson("""{"reachable":"paused"}""")
        assertNull(me.sessionId)
        assertEquals("paused", me.reachable)
    }

    @Test
    fun abortPathUsesConnectMeSessionNotSessionsList() {
        assertEquals(
            "/api/sessions/sid-live-turn/abort?reason=stop",
            ConnectMe.abortPath("sid-live-turn"),
        )
        assertEquals("/api/stop", ConnectMe.abortPath(null))
        assertEquals("/api/stop", ConnectMe.abortPath("  "))
        assertEquals("/api/stop", ConnectMe.abortPath(""))
        val listed = ConnectMe.abortPath("sid-live-turn")
        assertTrue("sessions?limit" !in listed)
        assertTrue("/api/sessions?" !in listed)
    }
}
