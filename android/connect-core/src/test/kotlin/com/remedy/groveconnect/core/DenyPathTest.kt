package com.remedy.groveconnect.core

import kotlin.test.Test
import kotlin.test.assertFalse
import kotlin.test.assertTrue

class DenyPathTest {
    private val forbidden = listOf(
        "/api/computer/jobs/next",
        "/computer/jobs/next",
        "/api/computer/jobs/next?wait_ms=5000",
        "/api/computer/jobs/next?wait_ms=5000&take=1",
        "/api/computer/jobs/next/",
        "//api/computer/jobs/next",
        "/API/COMPUTER/JOBS/NEXT",
        "/api/computer/jobs/next#x",
        "api/computer/jobs/next",
        "/api/computer/jobs/next?take=1",
        "/%61pi/computer/jobs/next",
        "/api/computer/jobs%2Fnext",
        "/api/computer/jobs%2Fnext?wait_ms=1",
        "/api/computer/jobs/next/ ",
        "/api/auth/local-bootstrap",
        "/api/computer/host/hello",
        "/api/computer/ui/command",
        "/api/computer/jobs/abc/complete",
        "/api/connect",
        "/api/connect/pair/start",
        "/api/connect/pause",
        "/api/connect/resume",
        "/api/connect/addresses",
        "/connect/pair/start",
        "/connect/pause",
        "/API/CONNECT/PAIR/START",
        "/api/connect%2Fpair%2Fstart",
        "/%61pi/connect/pair/start",
    )

    private val allowed = listOf(
        "/api/sessions",
        "/api/sessions/abc/messages",
        "/api/jobs",
        "/next",
        "/api/computer/capture",
        "/api/approvals",
        "/?connect=1",
        "/api/sessions/next-week",
        "/api/computer/snapshot",
        "/connect/me",
        "/api/connect/me",
        "/connect/preview",
        "/api/connect/preview",
        // Self-revoke is allowed through the coarse phone gate: the *server*
        // enforces the caller==target check (pipe.py), so the Settings
        // "Revoke this phone" button can reach the PC. Pre-blocking it here
        // made revoke fail client-side before it ever crossed the pipe.
        "/api/connect/devices/abc/revoke",
        "/connect/devices/abc/revoke",
    )

    @Test
    fun jobsNextFamilyForbidden() {
        for (p in forbidden) {
            assertTrue(DenyPath.isForbidden(p), "should deny $p")
        }
    }

    @Test
    fun adjacentPathsNotForbidden() {
        for (p in allowed) {
            assertFalse(DenyPath.isForbidden(p), "should allow $p")
        }
    }

    @Test
    fun postJobsNextStillForbidden() {
        assertTrue(DenyPath.isForbidden("/api/computer/jobs/next", "POST"))
    }
}
