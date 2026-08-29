package com.remedy.groveconnect.core

/**
 * Pairing QR / paste text:
 *
 * ```
 * remedy-connect/1
 * hp=<urlsafe b64 of 32-byte host pub>
 * ps=<urlsafe b64 of 32-byte pair secret>
 * lan=<ipv4:port>
 * v6=<optional>
 * relay=<host:port>   (optional; owner-run splice, no secrets)
 * exp=<unix>
 * ```
 */
data class QrPayload(
    val hostPub: ByteArray,
    val pairSecret: ByteArray,
    val lanHost: String,
    val lanPort: Int,
    val v6: String?,
    val relayHost: String?,
    val relayPort: Int?,
    val expUnix: Long,
) {
    override fun equals(other: Any?): Boolean {
        if (this === other) return true
        if (other !is QrPayload) return false
        return hostPub.contentEquals(other.hostPub) &&
            pairSecret.contentEquals(other.pairSecret) &&
            lanHost == other.lanHost &&
            lanPort == other.lanPort &&
            v6 == other.v6 &&
            relayHost == other.relayHost &&
            relayPort == other.relayPort &&
            expUnix == other.expUnix
    }

    override fun hashCode(): Int {
        var r = hostPub.contentHashCode()
        r = 31 * r + pairSecret.contentHashCode()
        r = 31 * r + lanHost.hashCode()
        r = 31 * r + lanPort
        r = 31 * r + (v6?.hashCode() ?: 0)
        r = 31 * r + (relayHost?.hashCode() ?: 0)
        r = 31 * r + (relayPort ?: 0)
        r = 31 * r + expUnix.hashCode()
        return r
    }

    companion object {
        fun parse(text: String, nowUnix: Long = System.currentTimeMillis() / 1000L): QrPayload {
            val lines = text.replace("\r\n", "\n").replace('\r', '\n')
                .split('\n')
                .map { it.trim() }
                .filter { it.isNotEmpty() }
            if (lines.isEmpty() || lines[0] != Protocol.QR_HEADER) {
                throw QrException("Not a Grove Connect pairing code.")
            }
            val fields = linkedMapOf<String, String>()
            for (line in lines.drop(1)) {
                val eq = line.indexOf('=')
                if (eq <= 0) continue
                fields[line.substring(0, eq).trim().lowercase()] = line.substring(eq + 1).trim()
            }
            val hp = fields["hp"] ?: throw QrException("Pairing code is missing the PC key (hp).")
            val ps = fields["ps"] ?: throw QrException("Pairing code is missing the pair secret (ps).")
            val expRaw = fields["exp"] ?: throw QrException("Pairing code is missing an expiry (exp).")
            val lan = fields["lan"] ?: throw QrException("Pairing code is missing the LAN address (lan).")
            val hostPub = decodeKey(hp, "hp")
            val pairSecret = decodeKey(ps, "ps")
            val exp = expRaw.toLongOrNull() ?: throw QrException("Pairing code expiry is not a number.")
            if (nowUnix > exp) throw QrException("This pairing code has expired. Ask the PC for a new one.")
            val (host, port) = parseLan(lan)
            val v6 = fields["v6"]?.takeIf { it.isNotEmpty() }
            val relayRaw = fields["relay"]?.takeIf { it.isNotEmpty() }
            val relay = relayRaw?.let { parseRelay(it) }
            if (relayRaw != null && (
                    relayRaw.contains("bearer", ignoreCase = true) ||
                        relayRaw.contains("local_api_token", ignoreCase = true)
                    )
            ) {
                throw QrException("Relay line must not carry secrets.")
            }
            return QrPayload(
                hostPub, pairSecret, host, port, v6,
                relay?.first, relay?.second, exp,
            )
        }

        private fun decodeKey(raw: String, label: String): ByteArray {
            val bytes = try {
                UrlSafeB64.decode(raw)
            } catch (_: Exception) {
                throw QrException("Pairing field $label is not valid urlsafe base64.")
            }
            if (bytes.size != Protocol.KEY_LEN) {
                throw QrException("Pairing field $label must be 32 bytes.")
            }
            return bytes
        }

        private fun parseLan(lan: String): Pair<String, Int> {
            val idx = lan.lastIndexOf(':')
            if (idx <= 0 || idx == lan.lastIndex) {
                throw QrException("LAN address must be ipv4:port.")
            }
            val host = lan.substring(0, idx)
            val port = lan.substring(idx + 1).toIntOrNull()
                ?: throw QrException("LAN port is not a number.")
            if (port !in 1..65535) throw QrException("LAN port is out of range.")
            val parts = host.split('.')
            if (parts.size != 4 || parts.any { it.toIntOrNull()?.let { n -> n in 0..255 } != true }) {
                throw QrException("LAN host must be IPv4.")
            }
            return host to port
        }

        private fun parseRelay(raw: String): Pair<String, Int> {
            val text = raw.trim()
            if (text.startsWith("http://", ignoreCase = true) ||
                text.startsWith("https://", ignoreCase = true)
            ) {
                throw QrException("Relay is a TCP splice, not HTTP.")
            }
            val idx = text.lastIndexOf(':')
            if (idx <= 0 || idx == text.lastIndex) {
                throw QrException("Relay must be host:port.")
            }
            val host = text.substring(0, idx).trim().trim('[', ']')
            val port = text.substring(idx + 1).toIntOrNull()
                ?: throw QrException("Relay port is not a number.")
            if (port !in 1..65535) throw QrException("Relay port is out of range.")
            if (host == "0.0.0.0" || host == "*" || host == "::") {
                throw QrException("Relay must not be a wildcard.")
            }
            return host to port
        }
    }
}

class QrException(message: String) : Exception(message)
