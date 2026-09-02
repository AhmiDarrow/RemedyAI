package com.remedy.groveconnect.core

/**
 * Host pane flags. A pane the host turned off is hidden on the phone.
 * Missing keys stay visible (only an explicit false / omission from a list hides).
 */
data class PaneFlags(
    val flags: Map<String, Boolean>,
) {
    fun isVisible(id: String): Boolean = flags[id] ?: true

    fun hidden(): List<String> = KNOWN.filterNot { isVisible(it) }

    fun visible(): List<String> = KNOWN.filter { isVisible(it) }

    companion object {
        /** Mirrors PANE_KEYS in src/remedy/connect/panes.py on the PC. */
        val KNOWN: List<String> = listOf(
            "live_ui",
            "chat",
            "approvals",
            "sessions",
            "rails",
            "computer_preview",
            "settings_write",
        )

        fun allOn(): PaneFlags = PaneFlags(KNOWN.associateWith { true })

        /**
         * Accepts:
         * - JSON object `{"chat": true, "terminal": false}`
         * - JSON array `["chat","browser"]` (only those on)
         * - comma-separated `chat,browser`
         * - already-parsed map
         */
        fun parse(raw: Any?): PaneFlags {
            when (raw) {
                null -> return allOn()
                is PaneFlags -> return raw
                is Map<*, *> -> {
                    val m = linkedMapOf<String, Boolean>()
                    for (id in KNOWN) m[id] = true
                    for ((k, v) in raw) {
                        val key = k?.toString()?.lowercase()?.trim() ?: continue
                        m[key] = truthy(v)
                    }
                    return PaneFlags(m)
                }
                is Collection<*> -> {
                    val on = raw.mapNotNull { it?.toString()?.lowercase()?.trim() }.filter { it.isNotEmpty() }.toSet()
                    return fromAllowlist(on)
                }
                is Array<*> -> {
                    val on = raw.mapNotNull { it?.toString()?.lowercase()?.trim() }.filter { it.isNotEmpty() }.toSet()
                    return fromAllowlist(on)
                }
                is String -> {
                    val s = raw.trim()
                    if (s.isEmpty()) return allOn()
                    if (s.startsWith("{")) return parseObjectString(s)
                    if (s.startsWith("[")) return parseArrayString(s)
                    val on = s.split(',', ' ').map { it.trim().lowercase() }.filter { it.isNotEmpty() }.toSet()
                    return fromAllowlist(on)
                }
                else -> return allOn()
            }
        }

        private fun fromAllowlist(on: Set<String>): PaneFlags {
            val m = linkedMapOf<String, Boolean>()
            val extra = on.filter { it !in KNOWN }
            for (id in KNOWN) m[id] = id in on
            for (id in extra) m[id] = true
            return PaneFlags(m)
        }

        private fun truthy(v: Any?): Boolean = when (v) {
            null -> false
            is Boolean -> v
            is Number -> v.toInt() != 0
            is String -> v.trim().lowercase() in setOf("1", "true", "yes", "on")
            else -> false
        }

        private fun parseArrayString(s: String): PaneFlags {
            val inner = s.trim().removePrefix("[").removeSuffix("]")
            val on = inner.split(',')
                .map { it.trim().trim('"').trim('\'').lowercase() }
                .filter { it.isNotEmpty() }
                .toSet()
            return fromAllowlist(on)
        }

        private fun parseObjectString(s: String): PaneFlags {
            val inner = s.trim().removePrefix("{").removeSuffix("}")
            if (inner.isBlank()) return allOn()
            val m = linkedMapOf<String, Boolean>()
            for (id in KNOWN) m[id] = true
            for (part in inner.split(',')) {
                val idx = part.indexOf(':')
                if (idx <= 0) continue
                val key = part.substring(0, idx).trim().trim('"').trim('\'').lowercase()
                val value = part.substring(idx + 1).trim().trim('"').trim('\'')
                if (key.isNotEmpty()) m[key] = truthy(value)
            }
            return PaneFlags(m)
        }
    }
}
