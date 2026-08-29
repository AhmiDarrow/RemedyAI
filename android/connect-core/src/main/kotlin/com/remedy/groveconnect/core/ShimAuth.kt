package com.remedy.groveconnect.core

import java.security.SecureRandom

/**
 * High-entropy token the phone WebView presents on every loopback-shim request.
 *
 * Load only `http://127.0.0.1:{port}/{token}/?connect=1`. Unauthenticated GET /
 * must not mint `grove_shim`. Subsequent SPA fetches may use the cookie or
 * `X-Grove-Shim` after a request that already carried the token.
 */
object ShimAuth {
    const val COOKIE_NAME = "grove_shim"
    const val HEADER_NAME = "X-Grove-Shim"
    const val TOKEN_HEX_LEN = 32

    fun newToken(): String {
        val raw = ByteArray(TOKEN_HEX_LEN / 2)
        SecureRandom().nextBytes(raw)
        return toHex(raw)
    }

    fun webViewUrl(port: Int, token: String): String =
        "http://127.0.0.1:$port/$token/?connect=1"

    fun allowed(target: String, headers: String, token: String): Boolean {
        if (!isTokenShape(token)) return false
        return pathHasToken(target, token) ||
            headerHasToken(headers, token) ||
            cookieHasToken(headers, token)
    }

    /** Strip the token path prefix (and a leftover `shim=` query) before the pipe. */
    fun strip(target: String, token: String): String {
        val (path, suffix) = splitTarget(target)
        val segs = path.split('/').filter { it.isNotEmpty() }
        val newPath = if (
            segs.isNotEmpty() && isTokenShape(token) &&
            tokenEquals(percentDecode(segs[0]), token)
        ) {
            val rest = segs.drop(1)
            if (rest.isEmpty()) "/" else "/" + rest.joinToString("/")
        } else {
            if (path.isEmpty()) "/" else path
        }
        return newPath + stripShimQuery(suffix)
    }

    fun cookieValue(token: String): String =
        "$COOKIE_NAME=$token; HttpOnly; Path=/; SameSite=Strict"

    fun pathHasToken(target: String, token: String): Boolean {
        if (!isTokenShape(token)) return false
        val (path, _) = splitTarget(target)
        val segs = path.split('/').filter { it.isNotEmpty() }
        if (segs.isEmpty()) return false
        return tokenEquals(percentDecode(segs[0]), token)
    }

    private fun headerHasToken(headers: String, token: String): Boolean {
        for (line in headers.lineSequence()) {
            if (!line.startsWith("$HEADER_NAME:", ignoreCase = true)) continue
            if (tokenEquals(line.substringAfter(":").trim(), token)) return true
        }
        return false
    }

    private fun cookieHasToken(headers: String, token: String): Boolean {
        for (line in headers.lineSequence()) {
            if (!line.startsWith("Cookie:", ignoreCase = true)) continue
            val raw = line.substringAfter(":").trim()
            for (part in raw.split(';')) {
                val kv = part.trim()
                val eq = kv.indexOf('=')
                if (eq <= 0) continue
                val name = kv.substring(0, eq).trim()
                val value = kv.substring(eq + 1).trim()
                if (name.equals(COOKIE_NAME, ignoreCase = true) && tokenEquals(value, token)) {
                    return true
                }
            }
        }
        return false
    }

    private fun splitTarget(target: String): Pair<String, String> {
        val q = target.indexOf('?')
        val h = target.indexOf('#')
        val cut = when {
            q >= 0 && h >= 0 -> minOf(q, h)
            q >= 0 -> q
            h >= 0 -> h
            else -> -1
        }
        if (cut < 0) return target to ""
        return target.substring(0, cut) to target.substring(cut)
    }

    private fun stripShimQuery(suffix: String): String {
        if (suffix.isEmpty()) return suffix
        val hashIdx = suffix.indexOf('#')
        val hash = if (hashIdx >= 0) suffix.substring(hashIdx) else ""
        val beforeHash = if (hashIdx >= 0) suffix.substring(0, hashIdx) else suffix
        if (!beforeHash.startsWith("?")) return suffix
        val kept = beforeHash.substring(1).split('&').filterNot {
            it == "shim" || it.startsWith("shim=")
        }
        val query = if (kept.isEmpty()) "" else "?${kept.joinToString("&")}"
        return query + hash
    }

    private fun isTokenShape(token: String): Boolean =
        token.length == TOKEN_HEX_LEN && token.all { it in '0'..'9' || it in 'a'..'f' }

    private fun tokenEquals(got: String, expected: String): Boolean {
        val a = got.lowercase().toByteArray(Charsets.US_ASCII)
        val b = expected.lowercase().toByteArray(Charsets.US_ASCII)
        return Crypto.constantTimeEquals(a, b)
    }

    private fun percentDecode(s: String): String = DenyPath.percentDecode(s)

    private fun toHex(raw: ByteArray): String =
        raw.joinToString("") { b -> "%02x".format(b.toInt() and 0xff) }
}
