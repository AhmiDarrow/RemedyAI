package com.remedy.groveconnect.ui

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.speech.RecognizerIntent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.imePadding
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.MoreHoriz
import androidx.compose.material.icons.filled.Person
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Visibility
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.NavigationBarItemDefaults
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableIntStateOf
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.runtime.snapshots.SnapshotStateList
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.remedy.groveconnect.api.ApiResult
import com.remedy.groveconnect.api.ChatMessage
import com.remedy.groveconnect.api.Decision
import com.remedy.groveconnect.api.GoalRow
import com.remedy.groveconnect.api.HeadOverview
import com.remedy.groveconnect.api.PartnerApi
import com.remedy.groveconnect.api.RemedyApi
import com.remedy.groveconnect.api.SessionsApi
import com.remedy.groveconnect.api.TurnMark
import com.remedy.groveconnect.api.canStop
import com.remedy.groveconnect.api.decisionHeadline
import com.remedy.groveconnect.api.explainCommand
import com.remedy.groveconnect.api.headSummary
import com.remedy.groveconnect.api.isGenericReason
import com.remedy.groveconnect.api.needsOwner
import com.remedy.groveconnect.api.relativeWhen
import com.remedy.groveconnect.api.stateWords
import com.remedy.groveconnect.connect.Reachable
import com.remedy.groveconnect.connect.RemoteState
import com.remedy.groveconnect.ui.theme.Accent
import com.remedy.groveconnect.ui.theme.BgPrimary
import com.remedy.groveconnect.ui.theme.BgSecondary
import com.remedy.groveconnect.ui.theme.BgTertiary
import com.remedy.groveconnect.ui.theme.Border
import com.remedy.groveconnect.ui.theme.Error
import com.remedy.groveconnect.ui.theme.Success
import com.remedy.groveconnect.ui.theme.TextMuted
import com.remedy.groveconnect.ui.theme.TextPrimary
import com.remedy.groveconnect.ui.theme.TextSecondary
import com.remedy.groveconnect.ui.theme.Warning
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/*
 * The phone portal: a line to Remedy, not a copy of the desktop.
 *
 *   Talk — Remedy's own conversation, streamed, with voice input; resumes
 *          after the connection drops.
 *   Work — what she is doing: side jobs (stoppable), goals, routines and
 *          "while you were away".
 *   More — the full remote the app had before (any chat, history, terminal
 *          when allowed, settings). Nothing was removed.
 *
 * A question from Remedy shows as one card at a time above Talk and Work.
 */

enum class PortalTab(val label: String) { Talk("Talk"), Work("Work"), More("More") }

@Composable
fun HomeScreen(
    state: RemoteState,
    api: RemedyApi,
    onClose: () -> Unit,
    onStop: () -> Unit,
    onRefresh: () -> Unit,
) {
    var tab by remember { mutableStateOf(PortalTab.Talk) }
    val partner = remember(api) { PartnerApi(api) }
    val sessions = remember(api) { SessionsApi(api) }
    var head by remember { mutableStateOf<HeadOverview?>(null) }
    var decisions by remember { mutableStateOf<List<Decision>>(emptyList()) }
    var refreshKey by remember { mutableIntStateOf(0) }
    val connected = state.shimUrl != null
    val canTalk = state.panes.isVisible("chat") && state.panes.isVisible("sessions")
    val canSee = state.panes.isVisible("live_ui")

    // One light poll for the whole portal: Remedy's head + questions. The PC
    // does the work; the phone only reads a small JSON every few seconds.
    LaunchedEffect(connected, refreshKey) {
        if (!connected) return@LaunchedEffect
        while (true) {
            val (h, d) = withContext(Dispatchers.IO) {
                (if (canSee) partner.head() else null) to partner.decisions()
            }
            if (h != null) head = h
            decisions = d
            delay(if (head?.headState == "working" || head?.activeChildren?.isNotEmpty() == true) 3000 else 6000)
        }
    }

    Column(Modifier.fillMaxSize().background(BgPrimary).statusBarsPadding()) {
        Row(
            Modifier.fillMaxWidth().padding(start = 16.dp, end = 4.dp, top = 8.dp, bottom = 4.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                "Remedy",
                color = TextPrimary,
                fontWeight = FontWeight.SemiBold,
                fontSize = 22.sp,
                modifier = Modifier.semantics { heading() },
            )
            Spacer(Modifier.width(10.dp))
            StatusDot(state.reachable)
            Spacer(Modifier.width(6.dp))
            Text(reachWords(state.reachable), color = TextMuted, fontSize = 13.sp)
            Spacer(Modifier.weight(1f))
            IconButton(onClick = onClose) {
                Icon(Icons.Filled.Close, contentDescription = "Close the remote", tint = TextSecondary)
            }
        }
        if (!connected) {
            Text(
                state.error ?: "Reconnecting to your PC…",
                color = Warning,
                fontSize = 14.sp,
                modifier = Modifier.fillMaxWidth().background(BgSecondary).padding(horizontal = 16.dp, vertical = 8.dp)
                    .semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
        if (tab != PortalTab.More) {
            DecisionDock(
                decisions,
                partner,
                onAnswered = {
                    refreshKey++
                    onRefresh()
                },
            )
        }
        Box(Modifier.weight(1f)) {
            when (tab) {
                PortalTab.Talk ->
                    if (canTalk) {
                        TalkTab(sessions, partner, head, decisions.size, connected)
                    } else {
                        Blocked("Talking from the phone is turned off for this device on your PC (Settings → Connect).")
                    }
                PortalTab.Work ->
                    if (canSee) {
                        WorkTab(partner, head, connected, onChanged = { refreshKey++ })
                    } else {
                        Blocked("Seeing Remedy's work from the phone is turned off for this device on your PC (Settings → Connect).")
                    }
                PortalTab.More -> MoreRemote(state, api, onClose, onStop)
            }
        }
        NavigationBar(containerColor = BgSecondary, contentColor = TextPrimary) {
            PortalTab.entries.forEach { t ->
                val badge = when (t) {
                    PortalTab.Work -> (head?.activityUnseen ?: 0)
                    else -> 0
                }
                NavigationBarItem(
                    selected = tab == t,
                    onClick = { tab = t },
                    icon = { Icon(portalIcon(t), contentDescription = null, modifier = Modifier.size(24.dp)) },
                    label = {
                        Text(if (badge > 0) "${t.label} · $badge" else t.label, fontSize = 13.sp)
                    },
                    colors = NavigationBarItemDefaults.colors(
                        selectedIconColor = Accent,
                        selectedTextColor = TextPrimary,
                        indicatorColor = BgTertiary,
                        unselectedIconColor = TextMuted,
                        unselectedTextColor = TextMuted,
                    ),
                )
            }
        }
    }
}

private fun portalIcon(t: PortalTab) = when (t) {
    PortalTab.Talk -> Icons.Filled.Person
    PortalTab.Work -> Icons.Filled.Visibility
    PortalTab.More -> Icons.Filled.MoreHoriz
}

internal fun reachWords(r: Reachable): String = when (r) {
    Reachable.Connecting -> "connecting"
    Reachable.OnLan -> "on your Wi-Fi"
    Reachable.OnRelay -> "away from home"
    Reachable.Paused -> "paused"
}

@Composable
internal fun Blocked(text: String) {
    Box(Modifier.fillMaxSize().padding(24.dp), contentAlignment = Alignment.Center) {
        Text(text, color = TextMuted, fontSize = 15.sp, lineHeight = 21.sp)
    }
}

// ---------------------------------------------------------------------------
// Decision card — one question at a time
// ---------------------------------------------------------------------------

@Composable
private fun DecisionDock(items: List<Decision>, partner: PartnerApi, onAnswered: () -> Unit) {
    var later by remember { mutableStateOf(false) }
    var seen by remember { mutableStateOf(emptySet<String>()) }
    var busy by remember { mutableStateOf(false) }
    var explainId by remember { mutableStateOf<String?>(null) }
    var flash by remember { mutableStateOf<String?>(null) }
    val scope = rememberCoroutineScope()

    // A question the owner has not seen yet brings the card back.
    LaunchedEffect(items) {
        val ids = items.map { it.id }.toSet()
        if ((ids - seen).isNotEmpty()) later = false
        seen = seen + ids
    }
    LaunchedEffect(flash) {
        if (flash != null) {
            delay(3000)
            flash = null
        }
    }

    val item = items.firstOrNull()
    if (item == null) {
        flash?.let {
            Text(
                it,
                color = TextSecondary,
                fontSize = 14.sp,
                modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 6.dp)
                    .semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
        return
    }
    if (later) {
        Row(
            Modifier.padding(horizontal = 16.dp, vertical = 6.dp).clip16().background(BgTertiary)
                .clickable { later = false }.padding(horizontal = 14.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Box(Modifier.size(9.dp).background(Warning, CircleShape))
            Spacer(Modifier.width(8.dp))
            Text(
                if (items.size == 1) "1 question waiting — tap to answer" else "${items.size} questions waiting — tap to answer",
                color = TextPrimary,
                fontSize = 14.sp,
            )
        }
        return
    }

    val sensitive = item.sensitive
    val explain = explainId == item.id
    fun decide(approve: Boolean) {
        if (busy) return
        busy = true
        scope.launch {
            val r = withContext(Dispatchers.IO) { partner.answer(item, approve) }
            busy = false
            flash = if (r.ok) {
                r.json?.optString("hint")?.takeIf { it.isNotBlank() }
                    ?: if (approve) "Okay — going ahead." else "Okay — I won't."
            } else {
                r.problem()
            }
            explainId = null
            onAnswered()
        }
    }

    Card(
        Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 6.dp)
            .semantics { liveRegion = LiveRegionMode.Polite },
        shape = RoundedCornerShape(18.dp),
        colors = CardDefaults.cardColors(containerColor = if (sensitive) Color(0xFF2A1F14) else BgSecondary),
        border = BorderStroke(1.dp, if (sensitive) Warning else Border),
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                if (sensitive) {
                    Icon(Icons.Filled.Lock, contentDescription = null, tint = Warning, modifier = Modifier.size(16.dp))
                    Spacer(Modifier.width(6.dp))
                }
                Text(
                    if (sensitive) "Your call — money, password or send" else "Remedy is asking",
                    color = if (sensitive) Warning else TextSecondary,
                    fontSize = 13.sp,
                    fontWeight = FontWeight.SemiBold,
                    modifier = Modifier.weight(1f),
                )
                if (items.size > 1) {
                    Text("1 of ${items.size}", color = TextMuted, fontSize = 13.sp)
                    Spacer(Modifier.width(4.dp))
                }
                TextButton(onClick = { later = true }) { Text("Later", color = TextMuted, fontSize = 14.sp) }
            }
            Text(
                decisionHeadline(item),
                color = TextPrimary,
                fontSize = 17.sp,
                lineHeight = 24.sp,
                fontWeight = FontWeight.Medium,
                modifier = Modifier.semantics { heading() },
            )
            Spacer(Modifier.height(4.dp))
            Text(
                when {
                    sensitive -> "Nothing happens until you say yes. This step always asks, every time."
                    item.blocking -> "Remedy is paused here until you decide."
                    else -> "Remedy will pick this up once you decide."
                },
                color = TextMuted,
                fontSize = 13.sp,
            )
            if (explain) {
                Spacer(Modifier.height(10.dp))
                Column(
                    Modifier.fillMaxWidth().clip16().background(BgTertiary).padding(12.dp),
                    verticalArrangement = Arrangement.spacedBy(6.dp),
                ) {
                    if (!isGenericReason(item.reason) && item.reason != decisionHeadline(item)) {
                        Text(item.reason, color = TextSecondary, fontSize = 14.sp)
                    }
                    Text("Exactly what Remedy would do:", color = TextMuted, fontSize = 12.sp)
                    val rows = explainCommand(item.command)
                    if (rows.isEmpty()) Text("(no details)", color = TextMuted, fontSize = 13.sp)
                    rows.take(12).forEach { (label, value) ->
                        SelectionContainer {
                            if (label.isEmpty()) {
                                Text(value.take(1200), color = TextPrimary, fontSize = 13.sp, fontFamily = FontFamily.Monospace)
                            } else {
                                Text("$label: ${value.take(600)}", color = TextPrimary, fontSize = 14.sp)
                            }
                        }
                    }
                }
            }
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp), modifier = Modifier.fillMaxWidth()) {
                Button(
                    onClick = { decide(true) },
                    enabled = !busy,
                    colors = ButtonDefaults.buttonColors(containerColor = Accent),
                    shape = RoundedCornerShape(14.dp),
                    modifier = Modifier.weight(1f).height(52.dp),
                ) { Text(if (busy) "…" else if (sensitive) "Yes, this once" else "Yes", fontSize = 16.sp, fontWeight = FontWeight.SemiBold) }
                OutlinedButton(
                    onClick = { decide(false) },
                    enabled = !busy,
                    shape = RoundedCornerShape(14.dp),
                    border = BorderStroke(1.dp, Border),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = TextPrimary),
                    modifier = Modifier.weight(1f).height(52.dp),
                ) { Text(if (sensitive) "Not now" else "No", fontSize = 16.sp) }
                OutlinedButton(
                    onClick = { explainId = if (explain) null else item.id },
                    shape = RoundedCornerShape(14.dp),
                    border = BorderStroke(1.dp, Border),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = TextSecondary),
                    modifier = Modifier.weight(1f).height(52.dp),
                ) { Text(if (explain) "Hide" else "Explain", fontSize = 16.sp) }
            }
        }
    }
}

private fun Modifier.clip16(): Modifier = this.clip(RoundedCornerShape(14.dp))
