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
    val panes: PaneFlags = PaneFlags.allOn(),
    val approvals: List<ApprovalItem> = emptyList(),
    val sessionId: String? = null,
    val error: String? = null,
    val lanLabel: String? = null,
)

class ConnectController(private val ctx: Context) {
    private val keys = DeviceKeys(ctx)
    private val store = PairStore(ctx)
    private val main = Handler(Looper.getMainLooper())
    private val io = Executors.newScheduledThreadPool(2) { r ->
        Thread(r, "grove-ctrl").apply { isDaemon = true }
    }
    private var client: ConnectClient? = null
    private var shim: LoopbackShim? = null
    private var poll: ScheduledFuture<*>? = null
    var state = RemoteState(paired = store.isPaired(), lanLabel = store.lastLan())
        private set
    var onChange: (() -> Unit)? = null

    fun unlock() {
        publish(state.copy(unlocked = true, error = null))
    }

    fun pair(text: String) {
        io.execute {
            try {
                val qr = QrPayload.parse(text)
                Pin.check(store.pinnedHostPub(), qr.hostPub)
                store.pinHost(qr.hostPub)
                store.saveLan(qr.lanHost, qr.lanPort)
                store.saveTailscale(qr.tailscaleHost, qr.tailscalePort)
                store.saveRelay(qr.relayHost, qr.relayPort)
                store.saveRdv(qr.rdvHosts)
                publish(state.copy(paired = true, error = null, lanLabel = "${qr.lanHost}:${qr.lanPort}"))
                open(qr)
            } catch (e: Exception) {
                publish(state.copy(error = e.message ?: "Pairing failed"))
            }
        }
    }

    fun unpair() {
        shutdown()
        store.clearPair()
        publish(RemoteState(unlocked = true, paired = false))
    }

    fun connectLast() {
        val lan = store.lastLan()
        val hp = store.pinnedHostPub()
        if (lan == null || hp == null) return
        val deviceId = store.deviceId()
        if (deviceId.isNullOrBlank()) {
            publish(state.copy(error = "Paste a fresh pairing code — the secret is not stored."))
            return
        }
        io.execute {
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
                client = c
                val sh = LoopbackShim(c)
                sh.start()
                shim = sh
                ConnectForegroundService.start(ctx)
                val remote = c.via == "relay" || c.via == "rdv" || c.via == "tailscale"
                val reachable = if (remote) Reachable.OnRelay else Reachable.OnLan
                publish(
                    state.copy(
                        reachable = reachable,
                        shimUrl = sh.webViewUrl(),
                        lanLabel = when (c.via) {
                            "tailscale" -> "via Tailscale"
                            "relay", "rdv" -> "via relay"
                            else -> lan
                        },
                        paired = true,
                    ),
                )
                startPoll()
            } catch (e: Exception) {
                shutdownInner()
                publish(
                    state.copy(
                        reachable = Reachable.Paused,
                        error = e.message ?: "Could not reach the PC. Check that Remedy is running with Connect enabled, and that you have internet or Wi-Fi.",
                    ),
                )
            }
        }
    }

    fun open(qr: QrPayload) {
        io.execute {
            shutdownInner()
            publish(state.copy(reachable = Reachable.Connecting, error = null, shimUrl = null))
            try {
                val (priv, pub) = keys.staticPair()
                val c = ConnectClient(priv, pub)
                c.connect(qr, preferRelay = !NetProbe.isWifi(ctx))
                client = c
                val sh = LoopbackShim(c)
                sh.start()
                shim = sh
                ConnectForegroundService.start(ctx)
                val remote = c.via == "relay" || c.via == "rdv" || c.via == "tailscale"
                val reachable = if (remote) Reachable.OnRelay else Reachable.OnLan
                publish(
                    state.copy(
                        reachable = reachable,
                        shimUrl = sh.webViewUrl(),
                        lanLabel = when (c.via) {
                            "tailscale" -> "via Tailscale"
                            "relay", "rdv" -> "via relay"
                            else -> "${qr.lanHost}:${qr.lanPort}"
                        },
                        paired = true,
                    ),
                )
                startPoll()
            } catch (e: Exception) {
                shutdownInner()
                publish(
                    state.copy(
                        reachable = Reachable.Paused,
                        error = e.message ?: "Could not reach the PC. Check that Remedy is running with Connect enabled, and that you have internet or Wi-Fi.",
                    ),
                )
            }
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
                c.http("POST", ConnectMe.abortPath(sid), "Content-Type: application/json\r\n")
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
                client?.http(
                    "POST",
                    "/api/approvals/$id/resolve",
                    "Content-Type: application/json\r\n",
                    body,
                )
                refreshApprovals()
            } catch (e: Exception) {
                publish(state.copy(error = e.message ?: "Could not resolve"))
            }
        }
    }

    fun shutdown() {
        poll?.cancel(true)
        poll = null
        shutdownInner()
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
                refreshMe()
                refreshApprovals()
            } catch (_: Exception) {
            }
        }, 400, 2500, TimeUnit.MILLISECONDS)
    }

    private fun refreshMe() {
        val c = client ?: return
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
                        via == "relay" -> Reachable.OnRelay
                        else -> Reachable.OnLan
                    },
                ),
            )
            return
        }
        val sid = fetchSessionId(c)
        if (sid != null) publish(state.copy(sessionId = sid))
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

    private fun refreshApprovals() {
        val c = client ?: return
        val r = try {
            c.http("GET", "/api/approvals")
        } catch (_: Exception) {
            return
        }
        if (r.status !in 200..299) return
        val items = parseApprovals(r.body.toString(Charsets.UTF_8))
        publish(state.copy(approvals = items))
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
