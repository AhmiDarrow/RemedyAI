package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class PaneFlagsTest {
    @Test
    fun missingKeysStayVisible() {
        val p = PaneFlags.parse(mapOf("terminal" to false))
        assertTrue(p.isVisible("chat"))
        assertTrue(p.isVisible("browser"))
        assertFalse(p.isVisible("terminal"))
        assertTrue("terminal" in p.hidden())
        assertTrue("chat" in p.visible())
    }

    @Test
    fun objectHidesOffPanes() {
        val p = PaneFlags.parse("""{"chat": true, "browser": false, "files": false}""")
        assertTrue(p.isVisible("chat"))
        assertFalse(p.isVisible("browser"))
        assertFalse(p.isVisible("files"))
        assertTrue(p.isVisible("settings"))
    }

    @Test
    fun listFormOnlyThoseOn() {
        val p = PaneFlags.parse(listOf("chat", "browser"))
        assertTrue(p.isVisible("chat"))
        assertTrue(p.isVisible("browser"))
        assertFalse(p.isVisible("terminal"))
        assertFalse(p.isVisible("files"))
        assertFalse(p.isVisible("settings"))
    }

    @Test
    fun arrayString() {
        val p = PaneFlags.parse("""["chat","sessions"]""")
        assertTrue(p.isVisible("chat"))
        assertTrue(p.isVisible("sessions"))
        assertFalse(p.isVisible("studio"))
    }

    @Test
    fun nullMeansAllOn() {
        val p = PaneFlags.parse(null)
        assertTrue(PaneFlags.KNOWN.all { p.isVisible(it) })
        assertEquals(emptyList(), p.hidden())
    }
}
