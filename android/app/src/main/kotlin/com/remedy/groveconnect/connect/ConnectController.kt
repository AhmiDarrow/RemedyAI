package com.remedy.groveconnect.connect

import android.content.Context
import android.os.Handler
import android.os.Looper
import com.remedy.groveconnect.core.ConnectMe
import com.remedy.groveconnect.core.PaneFlags
import com.remedy.groveconnect.core.Pin
import com.remedy.groveconnect.core.QrPayload
import org.json.JSONArray
import org.json.JSONObject
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicLong

enum class Reachable { Connecting, OnLan, OnRelay, Paused }

data class ApprovalItem(
    val id: String,
    val summary: String,
    val sensitive: Boolean,
    val choices: List<String>,
    val sessionId: String?,
)

data class RemoteState(
    val unlocked: Boolean = false,
    val paired: Boolean = false,
    val reachable: Reachable = Reachable.Paused,
    val shimUrl: String? = null,
    val panes: PaneFlags = PaneFlags.defaults(),
    val approvals: List<ApprovalItem> = emptyList(),
    val sessionId: String? = null,
    val error: String? = null,
    val lanLabel: String? = null,
)

class ConnectController(private val ctx: Context) {
    private val keys = DeviceKeys(ctx)
    private val store = PairStore(ctx)
    private val main = Handler(Looper.getMainLooper())
    // All connection lifecycle work is serialized. The generation guard also
    // prevents a slow dial from resurrecting a connection after Unpair/Close.
    private val io = Executors.newSingleThreadScheduledExecutor { r ->
        Thread(r, "grove-ctrl").apply { isDaemon = true }
    }
    private val generation = AtomicLong(0)
    @Volatile
    private var client: ConnectClient? = null
    @Volatile
    private var shim: LoopbackShim? = null
    private var poll: ScheduledFuture<*>? = null
    @Volatile
    var state = RemoteState(paired = store.isPaired(), lanLabel = store.lastLan())
        private set
    var onChange: (() -> Unit)? = null

    fun unlock() {
        publish(state.copy(unlocked = true, error = null))
    }

    fun pair(text: String) {
        val op = generation.incrementAndGet()
        // Called from the UI while the activity is resumed: start the foreground
        // service here, not after a dial that may finish once we are backgrounded
        // (Android 12+ refuses a late startForeground and crashes the app).
        ConnectForegroundService.start(ctx)
        io.execute {
            try {
                val qr = QrPayload.parse(text)
                Pin.check(store.pinnedHostPub(), qr.hostPub)
                if (generation.get() != op) return@execute
                publish(state.copy(error = null, lanLabel = "${qr.lanHost}:${qr.lanPort}"))
                openNow(qr, op)
            } catch (e: Exception) {
                if (generation.get() == op) {
                    ConnectForegroundService.stop(ctx)
                    publish(state.copy(reachable = Reachable.Paused, error = e.message ?: "Pairing failed"))
                }
            }
        }
    }

    /**
     * Persist the pairing only once the Noise handshake succeeded. A QR that
     * never connects must not retarget every future reconnect (pinned key or
     * relay/rendezvous endpoints) — fail closed on a forged or stale code.
     */
    private fun persistPair(qr: QrPayload) {
        store.savePair(qr)
    }

    fun unpair() {
        shutdown()
        store.clearPair()
        publish(RemoteState(unlocked = true, paired = false))
    }

    fun connectLast() {
        val op = generation.incrementAndGet()
        val lan = store.lastLan()
        val hp = store.pinnedHostPub()
        if (lan == null || hp == null) return
        val deviceId = store.deviceId()
        if (deviceId.isNullOrBlank()) {
            publish(state.copy(error = "Paste a fresh pairing code — the secret is not stored."))
            return
        }
        // Start the foreground service while the app is still in front (see pair()).
        ConnectForegroundService.start(ctx)
        io.execute {
            if (generation.get() != op) return@execute
            shutdownInner()
            publish(state.copy(reachable = Reachable.Connecting, error = null, shimUrl = null))
            try {
                val idx = lan.lastIndexOf(':')
                val lanHost = if (idx > 0) lan.substring(0, idx) else lan
                val lanPort = if (idx > 0) lan.substring(idx + 1).toIntOrNull() ?: 7401 else 7401
                val relay = store.lastRelay()
                val rdv = store.lastRdv()
                val ts = store.lastTailscale()
                val (priv, pub) = keys.staticPair()
                val c = ConnectClient(priv, pub)
                c.reconnect(
                    hp, lanHost, lanPort, ts?.first, ts?.second, relay?.first, relay?.second, rdv, pub,
                    preferRelay = !NetProbe.isWifi(ctx),
                )
                if (generation.get() != op) {
                    c.close()
                    return@execute
                }
                client = c
                val sh = LoopbackShim(c)
                sh.start()
                shim = sh
                reconnectBackoffMs = RECONNECT_MIN_MS
                publish(
                    state.copy(
                        reachable = reachableFor(c.via),
                        shimUrl = sh.webViewUrl(),
                        lanLabel = viaLabel(c.via, lan),
                        paired = true,
                    ),
                )
                startPoll()
            } catch (e: Exception) {
                shutdownInner()
                if (generation.get() == op) publish(
                    state.copy(
                        reachable = Reachable.Paused,
                        error = e.message ?: "Could not reach the PC. Check that Remedy is running with Connect enabled, and that you have internet or Wi-Fi.",
                    ),
                ) else return@execute
                ConnectForegroundService.stop(ctx)
            }
        }
    }

    /** Relay, rendezvous and Tailscale are all "not on the LAN" for the UI. */
    private fun reachableFor(via: String?): Reachable =
        if (via == "relay" || via == "rdv" || via == "tailscale") Reachable.OnRelay else Reachable.OnLan

    private fun viaLabel(via: String?, lan: String?): String? = when (via) {
        "tailscale" -> "via Tailscale"
        "relay", "rdv" -> "via relay"
        else -> lan
    }

    /** Phone model shown on the PC's device list (never the user's name). */
    private fun deviceLabel(): String? {
        val model = android.os.Build.MODEL?.trim().orEmpty()
        return model.takeIf { it.isNotEmpty() }
    }

    fun open(qr: QrPayload) {
        val op = generation.incrementAndGet()
        ConnectForegroundService.start(ctx)
        io.execute {
            openNow(qr, op)
        }
    }

    private fun openNow(qr: QrPayload, op: Long) {
            if (generation.get() != op) return
            shutdownInner()
            publish(state.copy(reachable = Reachable.Connecting, error = null, shimUrl = null))
            try {
                val (priv, pub) = keys.staticPair()
                val c = ConnectClient(priv, pub)
                c.connect(qr, preferRelay = !NetProbe.isWifi(ctx), deviceName = deviceLabel())
                if (generation.get() != op) {
                    c.close()
                    return
                }
                persistPair(qr)
                client = c
                val sh = LoopbackShim(c)
                sh.start()
                shim = sh
                reconnectBackoffMs = RECONNECT_MIN_MS
                publish(
                    state.copy(
                        reachable = reachableFor(c.via),
                        shimUrl = sh.webViewUrl(),
                        lanLabel = viaLabel(c.via, "${qr.lanHost}:${qr.lanPort}"),
                        paired = true,
                    ),
                )
                startPoll()
            } catch (e: Exception) {
                shutdownInner()
                if (generation.get() == op) publish(
                    state.copy(
                        reachable = Reachable.Paused,
                        error = e.message ?: "Could not reach the PC. Check that Remedy is running with Connect enabled, and that you have internet or Wi-Fi.",
                    ),
                ) else return
                ConnectForegroundService.stop(ctx)
            }
    }

    fun stopGeneration() {
        io.execute {
            try {
                val c = client ?: return@execute
                var sid = state.sessionId
                if (sid.isNullOrBlank()) {
                    sid = fetchSessionId(c)
                }
                val response = c.http("POST", ConnectMe.abortPath(sid), "Content-Type: application/json\r\n")
                if (response.status !in 200..299) throw IllegalStateException("PC refused Stop (HTTP ${response.status})")
            } catch (e: Exception) {
                publish(state.copy(error = e.message ?: "Stop failed"))
            }
        }
    }

    /** One-shot refresh of connection + approvals (used by the native Home). */
    fun refreshNow() {
        io.execute {
            try {
                refreshMe()
                refreshApprovals()
            } catch (_: Exception) {
            }
        }
    }

    fun resolveApproval(id: String, approve: Boolean) {
        io.execute {
            try {
                val body = JSONObject().put("approve", approve).put("scope", "session").toString()
                    .toByteArray()
                val response = client?.http(
                    "POST",
                    "/api/approvals/$id/resolve",
                    "Content-Type: application/json\r\n",
                    body,
                ) ?: throw IllegalStateException("Phone is not connected")
                if (response.status !in 200..299) {
                    throw IllegalStateException("PC refused approval response (HTTP ${response.status})")
                }
                refreshApprovals()
            } catch (e: Exception) {
                publish(state.copy(error = e.message ?: "Could not resolve"))
            }
        }
    }

    fun shutdown() {
        generation.incrementAndGet()
        poll?.cancel(true)
        poll = null
        // Closing the live endpoints immediately unblocks a poll/request that
        // is ahead of the serialized cleanup task.
        try {
            shim?.stop()
        } catch (_: Exception) {
        }
        try {
            client?.close()
        } catch (_: Exception) {
        }
        io.execute { shutdownInner() }
        ConnectForegroundService.stop(ctx)
        publish(
            state.copy(
                reachable = Reachable.Paused,
                shimUrl = null,
                approvals = emptyList(),
            ),
        )
    }

    private fun shutdownInner() {
        try {
            shim?.stop()
        } catch (_: Exception) {
        }
        shim = null
        try {
            client?.close()
        } catch (_: Exception) {
        }
        client = null
    }

    private fun startPoll() {
        poll?.cancel(false)
        poll = io.scheduleWithFixedDelay({
            try {
                pollTick()
            } catch (_: Exception) {
            }
        }, 400, 2500, TimeUnit.MILLISECONDS)
    }

    /**
     * One poll: refresh state while the pipe is alive; when the reader has
     * died (PC paused, laptop slept, Wi-Fi dropped) tell the user and redial
     * with exponential backoff instead of showing a green dot on a dead socket.
     */
    private fun pollTick() {
        val c = client
        if (c != null && !c.closed) {
            val healthy = refreshMe() && refreshApprovals()
            if (healthy) {
                consecutiveProbeFailures = 0
                return
            }
            consecutiveProbeFailures++
            if (consecutiveProbeFailures < MAX_PROBE_FAILURES) return
            shutdownInner()
        }
        if (!store.isPaired() || store.deviceId().isNullOrBlank()) return
        val now = System.currentTimeMillis()
        if (state.reachable != Reachable.Connecting) {
            publish(
                state.copy(
                    reachable = Reachable.Connecting,
                    shimUrl = null,
                    error = "Connection to the PC dropped — reconnecting…",
                ),
            )
            nextReconnectAt = now
        }
        if (now < nextReconnectAt) return
        nextReconnectAt = now + reconnectBackoffMs
        reconnectBackoffMs = (reconnectBackoffMs * 2).coerceAtMost(RECONNECT_MAX_MS)
        redial(generation.get())
    }

    /** Synchronous redial on the poll thread; success resets the backoff. */
    private fun redial(op: Long) {
        if (generation.get() != op) return
        val lan = store.lastLan() ?: return
        val hp = store.pinnedHostPub() ?: return
        shutdownInner()
        try {
            val idx = lan.lastIndexOf(':')
            val lanHost = if (idx > 0) lan.substring(0, idx) else lan
            val lanPort = if (idx > 0) lan.substring(idx + 1).toIntOrNull() ?: 7401 else 7401
            val relay = store.lastRelay()
            val rdv = store.lastRdv()
            val ts = store.lastTailscale()
            val (priv, pub) = keys.staticPair()
            val c = ConnectClient(priv, pub)
            c.reconnect(
                hp, lanHost, lanPort, ts?.first, ts?.second, relay?.first, relay?.second, rdv, pub,
                preferRelay = !NetProbe.isWifi(ctx),
            )
            if (generation.get() != op) {
                c.close()
                return
            }
            client = c
            val sh = LoopbackShim(c)
            sh.start()
            shim = sh
            reconnectBackoffMs = RECONNECT_MIN_MS
            consecutiveProbeFailures = 0
            publish(
                state.copy(
                    reachable = reachableFor(c.via),
                    shimUrl = sh.webViewUrl(),
                    lanLabel = viaLabel(c.via, lan),
                    error = null,
                    paired = true,
                ),
            )
        } catch (e: Exception) {
            shutdownInner()
            if (generation.get() != op) return
            publish(
                state.copy(
                    reachable = Reachable.Connecting,
                    error = "Reconnecting… (${e.message ?: "PC unreachable"})",
                ),
            )
        }
    }

    @Volatile
    private var reconnectBackoffMs = RECONNECT_MIN_MS

    @Volatile
    private var nextReconnectAt = 0L

    private var consecutiveProbeFailures = 0

    private companion object {
        const val RECONNECT_MIN_MS = 2_000L
        const val RECONNECT_MAX_MS = 30_000L
        const val MAX_PROBE_FAILURES = 3
    }

    private fun refreshMe(): Boolean {
        val c = client ?: return false
        val me = try {
            val r = c.http("GET", "/connect/me")
            if (r.status in 200..299) ConnectMe.parseJson(r.body.toString(Charsets.UTF_8)) else null
        } catch (_: Exception) {
            null
        }
        if (me != null) {
            me.deviceId?.takeIf { it.isNotBlank() }?.let { store.saveDeviceId(it) }
            val via = client?.via
            publish(
                state.copy(
                    sessionId = me.sessionId ?: state.sessionId,
                    panes = me.panes,
                    reachable = when {
                        me.reachable.lowercase() == "paused" -> Reachable.Paused
                        else -> reachableFor(via)
                    },
                ),
            )
            return true
        }
        return false
    }

    private fun fetchSessionId(c: ConnectClient): String? {
        return try {
            val r = c.http("GET", "/connect/me")
            if (r.status !in 200..299) return null
            ConnectMe.parseJson(r.body.toString(Charsets.UTF_8)).sessionId
                ?.takeIf { it.isNotBlank() }
        } catch (_: Exception) {
            null
        }
    }

    private fun refreshApprovals(): Boolean {
        val c = client ?: return false
        val r = try {
            c.http("GET", "/api/approvals")
        } catch (_: Exception) {
            return false
        }
        if (r.status !in 200..299) return false
        val items = parseApprovals(r.body.toString(Charsets.UTF_8))
        publish(state.copy(approvals = items))
        return true
    }

    private fun parseApprovals(json: String): List<ApprovalItem> {
        val arr = try {
            JSONObject(json).optJSONArray("approvals") ?: JSONArray()
        } catch (_: Exception) {
            return emptyList()
        }
        val out = ArrayList<ApprovalItem>(arr.length())
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            val choices = o.optJSONArray("choices")
            val ch = mutableListOf<String>()
            if (choices != null) {
                for (j in 0 until choices.length()) ch += choices.getString(j)
            }
            if (ch.isEmpty()) {
                ch += "yes"
                ch += "no"
            }
            out += ApprovalItem(
                id = o.optString("id"),
                summary = o.optString("summary").ifBlank { o.optString("reason") },
                sensitive = o.optBoolean("sensitive"),
                choices = ch,
                sessionId = o.optString("session_id").ifBlank { null },
            )
        }
        return out
    }

    private fun publish(next: RemoteState) {
        state = next
        main.post { onChange?.invoke() }
    }
}
