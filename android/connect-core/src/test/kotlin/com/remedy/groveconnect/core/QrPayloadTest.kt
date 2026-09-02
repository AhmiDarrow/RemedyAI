package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertNull
import kotlin.test.assertTrue

class QrPayloadTest {
    private val hp = ByteArray(32) { 0x11 }
    private val ps = ByteArray(32) { 0x22 }
    private val now = 1_800_000_000L

    private fun qr(
        hpB64: String = UrlSafeB64.encode(hp),
        psB64: String = UrlSafeB64.encode(ps),
        lan: String? = "192.168.1.10:7401",
        exp: String? = (now + 3600).toString(),
        extra: String = "",
        header: String = Protocol.QR_HEADER,
    ): String {
        val lines = mutableListOf(header)
        if (hpB64.isNotEmpty()) lines += "hp=$hpB64"
        if (psB64.isNotEmpty()) lines += "ps=$psB64"
        if (lan != null) lines += "lan=$lan"
        if (exp != null) lines += "exp=$exp"
        if (extra.isNotEmpty()) lines += extra
        return lines.joinToString("\n")
    }

    @Test
    fun parseValidUnpadded() {
        val q = QrPayload.parse(qr(), nowUnix = now)
        assertTrue(q.hostPub.contentEquals(hp))
        assertTrue(q.pairSecret.contentEquals(ps))
        assertEquals("192.168.1.10", q.lanHost)
        assertEquals(7401, q.lanPort)
        assertNull(q.v6)
        assertEquals(now + 3600, q.expUnix)
    }

    @Test
    fun parseValidPaddedAndCrlf() {
        val paddedHp = java.util.Base64.getUrlEncoder().encodeToString(hp)
        val paddedPs = java.util.Base64.getUrlEncoder().encodeToString(ps)
        val text = qr(hpB64 = paddedHp, psB64 = paddedPs).replace("\n", "\r\n")
        val q = QrPayload.parse(text, nowUnix = now)
        assertTrue(q.hostPub.contentEquals(hp))
        assertTrue(q.pairSecret.contentEquals(ps))
    }

    @Test
    fun parseOptionalV6() {
        val q = QrPayload.parse(qr(extra = "v6=[fe80::1]:7401"), nowUnix = now)
        assertEquals("[fe80::1]:7401", q.v6)
    }

    @Test
    fun parseOptionalRelay() {
        val q = QrPayload.parse(qr(extra = "relay=192.0.2.9:7402"), nowUnix = now)
        assertEquals("192.0.2.9", q.relayHost)
        assertEquals(7402, q.relayPort)
    }

    @Test
    fun parseOptionalRdv() {
        val q = QrPayload.parse(
            qr(extra = "rdv=broker.emqx.io:1883;broker.hivemq.com:1883"),
            nowUnix = now,
        )
        assertEquals(listOf("broker.emqx.io" to 1883, "broker.hivemq.com" to 1883), q.rdvHosts)
    }

    @Test
    fun rdvEmptyWhenAbsent() {
        val q = QrPayload.parse(qr(), nowUnix = now)
        assertTrue(q.rdvHosts.isEmpty())
    }

    @Test
    fun parseOptionalTailscale() {
        val q = QrPayload.parse(qr(extra = "ts=100.101.102.103:7401"), nowUnix = now)
        assertEquals("100.101.102.103", q.tailscaleHost)
        assertEquals(7401, q.tailscalePort)
    }

    @Test
    fun tailscaleEmptyWhenAbsent() {
        val q = QrPayload.parse(qr(), nowUnix = now)
        assertNull(q.tailscaleHost)
        assertNull(q.tailscalePort)
    }

    @Test
    fun rejectSecretsInTailscale() {
        val e = assertFailsWith<QrException> {
            QrPayload.parse(qr(extra = "ts=100.101.102.103:7401;local_api_token x"), nowUnix = now)
        }
        assertTrue(e.message!!.contains("secrets"), e.message)
    }

    @Test
    fun rejectGarbageTailscale() {
        assertFailsWith<QrException> {
            QrPayload.parse(qr(extra = "ts=not-an-ip:7401"), nowUnix = now)
        }
    }

    @Test
    fun rejectSecretsInRdv() {
        val e = assertFailsWith<QrException> {
            QrPayload.parse(qr(extra = "rdv=broker.emqx.io:1883;bearer x"), nowUnix = now)
        }
        assertTrue(e.message!!.contains("secrets"), e.message)
    }

    @Test
    fun rejectGarbageRdv() {
        assertFailsWith<QrException> {
            QrPayload.parse(qr(extra = "rdv=not-a-host:port"), nowUnix = now)
        }
    }

    @Test
    fun rejectHttpRelay() {
        val e = assertFailsWith<QrException> {
            QrPayload.parse(qr(extra = "relay=https://example.com/x"), nowUnix = now)
        }
        assertTrue(e.message!!.contains("HTTP"), e.message)
    }

    @Test
    fun rejectMissingHp() {
        val e = assertFailsWith<QrException> { QrPayload.parse(qr(hpB64 = ""), nowUnix = now) }
        assertTrue(e.message!!.contains("hp"), e.message)
    }

    @Test
    fun rejectMissingPs() {
        val e = assertFailsWith<QrException> { QrPayload.parse(qr(psB64 = ""), nowUnix = now) }
        assertTrue(e.message!!.contains("ps"), e.message)
    }

    @Test
    fun rejectMissingExp() {
        val e = assertFailsWith<QrException> { QrPayload.parse(qr(exp = null), nowUnix = now) }
        assertTrue(e.message!!.contains("exp"), e.message)
    }

    @Test
    fun rejectExpired() {
        val e = assertFailsWith<QrException> {
            QrPayload.parse(qr(exp = (now - 1).toString()), nowUnix = now)
        }
        assertTrue(e.message!!.contains("expired"), e.message)
    }

    @Test
    fun rejectExpiredAtBoundaryExclusive() {
        assertFailsWith<QrException> {
            QrPayload.parse(qr(exp = (now - 1).toString()), nowUnix = now)
        }
        val ok = QrPayload.parse(qr(exp = now.toString()), nowUnix = now)
        assertEquals(now, ok.expUnix)
    }

    @Test
    fun rejectWrongHeader() {
        assertFailsWith<QrException> { QrPayload.parse(qr(header = "remedy-connect/2"), nowUnix = now) }
    }

    @Test
    fun rejectShortKey() {
        assertFailsWith<QrException> {
            QrPayload.parse(qr(hpB64 = UrlSafeB64.encode(ByteArray(16))), nowUnix = now)
        }
    }

    @Test
    fun rejectDuplicateSecurityFields() {
        assertFailsWith<QrException> {
            QrPayload.parse(qr(extra = "hp=${UrlSafeB64.encode(hp)}"), nowUnix = now)
        }
    }

    @Test
    fun rejectOversizedCodeAndRendezvousList() {
        assertFailsWith<QrException> { QrPayload.parse("x".repeat(16 * 1024 + 1), nowUnix = now) }
        val endpoints = (1..9).joinToString(";") { "broker$it.example:1883" }
        assertFailsWith<QrException> { QrPayload.parse(qr(extra = "rdv=$endpoints"), nowUnix = now) }
    }
}
