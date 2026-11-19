package com.remedy.groveconnect.api

/** What to do when a streamed reply stops before its `done` frame. */
enum class ResumeAction {
    /** The link dropped mid-reply: wait, then re-attach after the last frame. */
    Retry,

    /** The PC says there is no such live turn: reload the conversation. */
    Reload,
}

/**
 * Decide from the stream error (pure; tested). HTTP 4xx from the attach route
 * means the turn is gone or never existed (400 "request_id required" when
 * nothing is running, 404 when the log is gone, 403 when the pane is off) —
 * retrying would not help. Everything else is the connection.
 */
fun resumeActionFor(error: String?): ResumeAction {
    val e = error.orEmpty().trim()
    val code = Regex("""^HTTP (\d{3})""").find(e)?.groupValues?.get(1)?.toIntOrNull()
    return if (code != null && code in 400..499) ResumeAction.Reload else ResumeAction.Retry
}

/** Wait before resume attempt [attempt] (0-based): 2s, 4s, 8s … capped at 20s. */
fun resumeDelayMs(attempt: Int): Long = (2_000L shl attempt.coerceIn(0, 4)).coerceAtMost(20_000L)

/** How many times to try re-attaching before reloading the conversation. */
const val MAX_RESUME_ATTEMPTS = 8
