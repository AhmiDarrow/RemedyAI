package com.remedy.groveconnect.core

import java.util.Base64

/**
 * Python-compatible urlsafe base64 for 32-byte keys.
 *
 * Encode matches `base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")`.
 * Decode accepts padded or unpadded, `+`/`/` or `-`/`_`.
 */
object UrlSafeB64 {
    private val encoder: Base64.Encoder = Base64.getUrlEncoder().withoutPadding()
    private val urlDecoder: Base64.Decoder = Base64.getUrlDecoder()
    private val mimeDecoder: Base64.Decoder = Base64.getDecoder()

    fun encode(raw: ByteArray): String = encoder.encodeToString(raw)

    fun decode(text: String): ByteArray {
        val trimmed = text.trim()
        if (trimmed.isEmpty()) throw IllegalArgumentException("empty b64")
        val normalized = trimmed.replace('+', '-').replace('/', '_')
        return try {
            urlDecoder.decode(normalized)
        } catch (_: IllegalArgumentException) {
            mimeDecoder.decode(trimmed)
        }
    }
}
