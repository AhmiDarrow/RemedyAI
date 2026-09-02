package com.remedy.groveconnect.ui

import com.remedy.groveconnect.connect.RemoteState
import com.remedy.groveconnect.core.PaneFlags
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class HomeTabPolicyTest {
    @Test
    fun chatRequiresChatAndSessionsCapabilities() {
        val sessionsOnly = RemoteState(panes = PaneFlags.parse(mapOf("chat" to false, "sessions" to true)))
        val chatOnly = RemoteState(panes = PaneFlags.parse(mapOf("chat" to true, "sessions" to false)))
        assertFalse(HomeTab.Chat.isAllowed(sessionsOnly))
        assertFalse(HomeTab.Chat.isAllowed(chatOnly))
        assertTrue(HomeTab.Sessions.isAllowed(sessionsOnly))
    }

    @Test
    fun groveUsesLiveUiRatherThanRails() {
        val liveOnly = RemoteState(panes = PaneFlags.parse(mapOf("live_ui" to true, "rails" to false)))
        val railsOnly = RemoteState(panes = PaneFlags.parse(mapOf("live_ui" to false, "rails" to true)))
        assertTrue(HomeTab.Grove.isAllowed(liveOnly))
        assertFalse(HomeTab.Grove.isAllowed(railsOnly))
    }
}
