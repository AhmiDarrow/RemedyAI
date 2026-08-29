package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class ShimAuthTest {
    private val token = "a".repeat(ShimAuth.TOKEN_HEX_LEN)
    private val other = "b".repeat(ShimAuth.TOKEN_HEX_LEN)

    private val unauthenticatedFamily = listOf(
        "/" to "",
        "/index.html" to "",
        "/INDEX.HTML" to "",
        "/assets/index-abc.js" to "",
        "/assets/index-abc.js.map" to "",
        "/?connect=1" to "",
        "/index.html?connect=1" to "",
        "/api/chat" to "",
        "/api/settings" to "",
        "/api/connect" to "",
        "/api/chat" to "Cookie: grove_shim=\r\n",
        "/api/chat" to "Cookie: grove_shim=$other\r\n",
        "/api/chat" to "Cookie: other=$token\r\n",
        "/api/chat" to "Cookie: notgrove_shim=$token\r\n",
        "/api/chat" to "Cookie: grove_shim=${token}extra\r\n",
        "/api/chat" to "X-Grove-Shim: $other\r\n",
        "/api/chat" to "X-Grove-Shim:\r\n",
        "/$other/?connect=1" to "",
        "/${token.substring(0, 31)}/?connect=1" to "",
        "/assets/$token/index.js" to "",
        "GET / HTTP/1.1" to "",
    )

    @Test
    fun newTokenIs32LowerHex() {
        val t = ShimAuth.newToken()
        assertEquals(ShimAuth.TOKEN_HEX_LEN, t.length)
        assertTrue(t.all { it in '0'..'9' || it in 'a'..'f' })
        assertTrue(ShimAuth.newToken() != t)
    }

    @Test
    fun webViewUrlIsPathPrefixNotBareRoot() {
        val url = ShimAuth.webViewUrl(9_001, token)
        assertEquals("http://127.0.0.1:9001/$token/?connect=1", url)
        assertFalse(url.contains("127.0.0.1:9001/?"))
        assertFalse(url.contains("shim="))
    }

    @Test
    fun unauthenticatedDocumentFamilyDenied() {
        for ((target, headers) in unauthenticatedFamily) {
            assertFalse(
                ShimAuth.allowed(target, headers, token),
                "should deny target=$target headers=$headers",
            )
        }
    }

    @Test
    fun tokenPathPrefixAllowsCompactSpaAndStrips() {
        val compact = "/$token/?connect=1"
        assertTrue(ShimAuth.allowed(compact, "", token))
        assertEquals("/?connect=1", ShimAuth.strip(compact, token))
        assertEquals("/", ShimAuth.strip("/$token", token))
        assertEquals("/", ShimAuth.strip("/$token/", token))
        assertEquals("/index.html", ShimAuth.strip("/$token/index.html", token))
        assertEquals(
            "/assets/index-abc.js",
            ShimAuth.strip("/$token/assets/index-abc.js", token),
        )
        assertEquals("/api/chat", ShimAuth.strip("/$token/api/chat", token))
    }

    @Test
    fun cookieAndHeaderAllowSubsequentSpaFetches() {
        assertTrue(ShimAuth.allowed("/assets/index-abc.js", "Cookie: grove_shim=$token\r\n", token))
        assertTrue(ShimAuth.allowed("/api/chat", "Cookie: grove_shim=$token; theme=dark\r\n", token))
        assertTrue(ShimAuth.allowed("/api/settings", "Cookie: theme=dark; grove_shim=$token\r\n", token))
        assertTrue(ShimAuth.allowed("/api/connect", "X-Grove-Shim: $token\r\n", token))
        assertTrue(ShimAuth.allowed("/?connect=1", "X-Grove-Shim: $token\r\n", token))
        assertEquals("/api/chat", ShimAuth.strip("/api/chat", token))
    }

    @Test
    fun emptyOrWrongLengthTokenNeverAllows() {
        assertFalse(ShimAuth.allowed("/$token/?connect=1", "", ""))
        assertFalse(ShimAuth.allowed("/", "Cookie: grove_shim=$token\r\n", "aa"))
        assertFalse(ShimAuth.allowed("/api/chat", "X-Grove-Shim: $token\r\n", token + "aa"))
    }
}
