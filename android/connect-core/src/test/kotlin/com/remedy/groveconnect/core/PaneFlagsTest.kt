package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class PaneFlagsTest {
    @Test
    fun missingKeysStayVisible() {
        val p = PaneFlags.parse(mapOf("live_ui" to false))
        assertTrue(p.isVisible("chat"))
        assertTrue(p.isVisible("rails"))
        assertFalse(p.isVisible("live_ui"))
        assertTrue("live_ui" in p.hidden())
        assertTrue("chat" in p.visible())
    }

    @Test
    fun objectHidesOffPanes() {
        val p = PaneFlags.parse("""{"chat": true, "rails": false, "computer_preview": false}""")
        assertTrue(p.isVisible("chat"))
        assertFalse(p.isVisible("rails"))
        assertFalse(p.isVisible("computer_preview"))
        assertFalse(p.isVisible("settings_write"))
    }

    @Test
    fun listFormOnlyThoseOn() {
        val p = PaneFlags.parse(listOf("chat", "rails"))
        assertTrue(p.isVisible("chat"))
        assertTrue(p.isVisible("rails"))
        assertFalse(p.isVisible("live_ui"))
        assertFalse(p.isVisible("computer_preview"))
        assertFalse(p.isVisible("settings_write"))
    }

    @Test
    fun arrayString() {
        val p = PaneFlags.parse("""["chat","sessions"]""")
        assertTrue(p.isVisible("chat"))
        assertTrue(p.isVisible("sessions"))
        assertFalse(p.isVisible("settings_write"))
    }

    @Test
    fun nullUsesPcSecurityDefaults() {
        val p = PaneFlags.parse(null)
        assertTrue(p.isVisible("chat"))
        assertTrue(p.isVisible("approvals"))
        assertFalse(p.isVisible("computer_preview"))
        assertFalse(p.isVisible("settings_write"))
    }

    @Test
    fun knownMatchesPcPaneKeys() {
        // Must stay in lockstep with PANE_KEYS in src/remedy/connect/panes.py.
        assertEquals(
            listOf("live_ui", "chat", "approvals", "sessions", "rails", "computer_preview", "settings_write"),
            PaneFlags.KNOWN,
        )
    }
}
