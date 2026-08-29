package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class SessionIdTest {
    @Test
    fun pairAndDeviceAre16BytesAndDistinct() {
        val hp = ByteArray(32) { 0x11 }
        val ps = ByteArray(32) { 0x22 }
        val dp = ByteArray(32) { 0x33 }
        val a = SessionId.pair(hp, ps)
        val b = SessionId.device(hp, dp)
        assertEquals(16, a.size)
        assertEquals(16, b.size)
        assertFalse(a.contentEquals(b))
        assertTrue(a.contentEquals(SessionId.pair(hp, ps)))
        assertFalse(a.contentEquals(ps.copyOf(16)))
    }
}
