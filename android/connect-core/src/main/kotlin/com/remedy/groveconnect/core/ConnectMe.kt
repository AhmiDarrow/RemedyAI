package com.remedy.groveconnect.core

/**
 * Shape of GET /connect/me (host injects Bearer locally). Missing fields are ok.
 */
data class ConnectMe(
    val sessionId: String?,
    val deviceId: String?,
    val panes: PaneFlags,
    val reachable: String,
) {
    companion object {
        /**
         * Native Stop: abort the /connect/me session_id, never GET /api/sessions[0].
         * Blank id falls back to POST /api/stop (process-active turn).
         */
        fun abortPath(sessionId: String?): String {
            val sid = sessionId?.trim().orEmpty()
            return if (sid.isNotEmpty()) {
                "/api/sessions/$sid/abort?reason=stop"
            } else {
                "/api/stop"
            }
        }

        fun parseJson(text: String): ConnectMe {
            val sessionId = jsonString(text, "session_id") ?: jsonString(text, "sessionId")
            val deviceId = jsonString(text, "device_id") ?: jsonString(text, "deviceId")
            val reachable = jsonString(text, "reachable") ?: "lan"
            val panesRaw = jsonObject(text, "panes") ?: jsonArray(text, "panes")
            val panes = when {
                panesRaw != null && panesRaw.startsWith("[") -> PaneFlags.parse(panesRaw)
                panesRaw != null -> PaneFlags.parse(panesRaw)
                else -> PaneFlags.allOn()
            }
            return ConnectMe(sessionId, deviceId, panes, reachable)
        }

        private fun jsonString(text: String, key: String): String? {
            val pat = Regex("\"$key\"\\s*:\\s*\"([^\"]*)\"")
            return pat.find(text)?.groupValues?.get(1)
        }

        private fun jsonObject(text: String, key: String): String? {
            val idx = Regex("\"$key\"\\s*:\\s*\\{").find(text)?.range?.last ?: return null
            return sliceBalanced(text, idx, '{', '}')
        }

        private fun jsonArray(text: String, key: String): String? {
            val idx = Regex("\"$key\"\\s*:\\s*\\[").find(text)?.range?.last ?: return null
            return sliceBalanced(text, idx, '[', ']')
        }

        private fun sliceBalanced(text: String, openIdx: Int, open: Char, close: Char): String {
            var depth = 0
            for (i in openIdx until text.length) {
                when (text[i]) {
                    open -> depth++
                    close -> {
                        depth--
                        if (depth == 0) return text.substring(openIdx, i + 1)
                    }
                }
            }
            return text.substring(openIdx)
        }
    }
}
