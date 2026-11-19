package com.remedy.groveconnect.connect

import com.remedy.groveconnect.ui.PortalTab
import com.remedy.groveconnect.ui.appendSpoken
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class NeedsYouTest {
    @Test
    fun onlyUnseenIdsAreNew() {
        assertEquals(setOf("b"), newDecisionIds(setOf("a"), listOf("a", "b", "")))
        assertTrue(newDecisionIds(setOf("a", "b"), listOf("b", "a")).isEmpty())
        assertTrue(newDecisionIds(emptySet(), emptyList()).isEmpty())
    }

    @Test
    fun sensitiveTitleWinsAndBodyIsNeverBlank() {
        assertEquals(
            "Remedy needs your OK — money, password or send",
            needsYouText("Pay £20", sensitive = true, count = 3).first,
        )
        assertEquals("Remedy has 2 questions for you", needsYouText("x", false, 2).first)
        assertEquals("Remedy has a question for you", needsYouText("x", false, 1).first)
        assertEquals("Open RemedyConnect to answer.", needsYouText("  ", false, 1).second)
    }

    @Test
    fun portalKeepsTalkFirstAndTheOldRemoteUnderMore() {
        assertEquals(listOf("Talk", "Work", "More"), PortalTab.entries.map { it.label })
    }

    @Test
    fun dictationAppendsToTypedText() {
        assertEquals("hello there", appendSpoken("", " hello there "))
        assertEquals("book a table for two", appendSpoken("book a table ", "for two"))
    }
}
