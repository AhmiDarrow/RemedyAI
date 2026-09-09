package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

/**
 * Public MQTT brokers accept wildcard subscribers, so the PC rotates the
 * rendezvous id it holds. The phone has to derive exactly the same id or it
 * can never meet the PC off-LAN.
 */
class RendezvousIdTest {
    private val hostPub = ByteArray(32) { 0x11 }
    private val devicePub = ByteArray(32) { 0x33 }

    @Test
    fun rendezvousIdRotatesWithTheBucket() {
        val now = SessionId.rendezvous(hostPub, devicePub, 472222L)
        val later = SessionId.rendezvous(hostPub, devicePub, 472223L)
        assertEquals(16, now.size)
        assertFalse(now.contentEquals(later), "the rendezvous id must rotate")
        assertTrue(
            now.contentEquals(SessionId.rendezvous(hostPub, devicePub, 472222L)),
            "the rendezvous id must be stable inside a bucket",
        )
    }

    @Test
    fun rendezvousIdIsNotTheRelayId() {
        val relay = SessionId.device(hostPub, devicePub)
        val rdv = SessionId.rendezvous(hostPub, devicePub, 472222L)
        assertFalse(relay.contentEquals(rdv), "a public-broker id must not equal the relay id")
    }

    /** Cross-checked against connect.SessionIDDeviceRDV in native/go. */
    @Test
    fun rendezvousIdMatchesTheHostDerivation() {
        val hex = SessionId.rendezvous(hostPub, devicePub, 472222L)
            .joinToString("") { "%02x".format(it) }
        assertEquals("120097d157d153d081b2e1e4f7d68635", hex)
    }

    @Test
    fun bucketsAreOneHourWide() {
        assertEquals(3600L, SessionId.RDV_BUCKET_SECONDS)
        assertEquals(1L, SessionId.rdvBucket(3600L))
        assertEquals(1L, SessionId.rdvBucket(7199L))
        assertEquals(2L, SessionId.rdvBucket(7200L))
    }
}
