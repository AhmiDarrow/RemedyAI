package com.remedy.groveconnect.core

/**
 * Paths the phone must never forward through the Connect pipe.
 * The PC host poller owns `jobs/next`; a remote WebView must not claim jobs.
 */
object DenyPath {
    private val JOBS_NEXT = Regex(
        """(?:^|/)(?:api/)?computer/jobs/next(?:/|$|\?|#)""",
        RegexOption.IGNORE_CASE,
    )
    private val JOBS_NEXT_END = Regex(
        """(?:^|/)(?:api/)?computer/jobs/next$""",
        RegexOption.IGNORE_CASE,
    )

    private val HOST = Regex("""(?:^|/)(?:api/)?computer/host(?:/|$|\?|#)""", RegexOption.IGNORE_CASE)
    private val BOOTSTRAP = Regex("""local-bootstrap""", RegexOption.IGNORE_CASE)
    private val JOBS_ANY = Regex("""(?:^|/)(?:api/)?computer/jobs/""", RegexOption.IGNORE_CASE)
    private val UI_COMMAND = Regex("""(?:^|/)(?:api/)?computer/ui/command""", RegexOption.IGNORE_CASE)
    private val CONNECT_ME = Regex(
        """(?:^|/)(?:api/)?connect/(?:me|preview)(?:/|$|\?|#)""",
        RegexOption.IGNORE_CASE,
    )
    private val CONNECT_MGMT = Regex(
        """(?:^|/)(?:api/)?connect(?:/|$|\?|#)""",
        RegexOption.IGNORE_CASE,
    )

    fun isForbidden(target: String, method: String = "GET"): Boolean {
        val path = normalize(target)
        if (path.isEmpty()) return false
        val decoded = percentDecode(path)
        val blobs = listOf(path, decoded, path + "/", decoded + "/")
        for (p in blobs) {
            if (JOBS_NEXT.containsMatchIn(p) || JOBS_NEXT_END.containsMatchIn(p)) return true
            if (HOST.containsMatchIn(p) || BOOTSTRAP.containsMatchIn(p)) return true
            if (JOBS_ANY.containsMatchIn(p) || UI_COMMAND.containsMatchIn(p)) return true
            if (CONNECT_MGMT.containsMatchIn(p) && !CONNECT_ME.containsMatchIn(p)) return true
        }
        if (method.equals("CONNECT", ignoreCase = true) ||
            method.equals("TRACE", ignoreCase = true)
        ) {
            return true
        }
        return false
    }

    fun forbiddenReason(target: String, method: String = "GET"): String? {
        if (!isForbidden(target, method)) return null
        return "This path is only for the PC host, not the phone remote."
    }

    internal fun normalize(target: String): String {
        var s = target.trim()
        val q = s.indexOf('?')
        val h = s.indexOf('#')
        val cut = when {
            q >= 0 && h >= 0 -> minOf(q, h)
            q >= 0 -> q
            h >= 0 -> h
            else -> -1
        }
        val query = if (q >= 0) s.substring(q) else ""
        if (cut >= 0) s = s.substring(0, cut)
        while (s.contains("//")) s = s.replace("//", "/")
        if (s.isEmpty()) s = "/"
        if (!s.startsWith("/")) s = "/$s"
        return s.lowercase() + query.lowercase()
    }

    internal fun percentDecode(s: String): String {
        val out = StringBuilder(s.length)
        var i = 0
        while (i < s.length) {
            val c = s[i]
            if (c == '%' && i + 2 < s.length) {
                val hex = s.substring(i + 1, i + 3)
                val v = hex.toIntOrNull(16)
                if (v != null) {
                    out.append(v.toChar())
                    i += 3
                    continue
                }
            }
            out.append(c)
            i++
        }
        return out.toString()
    }
}
