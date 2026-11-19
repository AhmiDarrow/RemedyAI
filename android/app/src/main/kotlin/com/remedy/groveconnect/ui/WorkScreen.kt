package com.remedy.groveconnect.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.PaddingValues
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Switch
import androidx.compose.material3.SwitchDefaults
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.heading
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.remedy.groveconnect.api.ApiResult
import com.remedy.groveconnect.api.GoalRow
import com.remedy.groveconnect.api.HeadOverview
import com.remedy.groveconnect.api.PartnerApi
import com.remedy.groveconnect.api.canStop
import com.remedy.groveconnect.api.needsOwner
import com.remedy.groveconnect.api.relativeWhen
import com.remedy.groveconnect.api.stateWords
import com.remedy.groveconnect.ui.theme.Accent
import com.remedy.groveconnect.ui.theme.BgSecondary
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

/** A phone-side "are you sure" before starting or stopping work on the PC. */
private data class Confirm(val title: String, val body: String, val yes: String, val run: () -> ApiResult)

/**
 * Work: what Remedy is doing, at a glance. Side jobs can be stopped, goals put
 * on (or taken off) autopilot, routines paused, resumed or run. Anything that
 * starts work asks on the phone first; pausing and stopping never start work.
 * New side jobs and routines are asked for in Talk, so they go through Remedy.
 */
@Composable
internal fun WorkTab(partner: PartnerApi, head: HeadOverview?, connected: Boolean, onChanged: () -> Unit) {
    var goals by remember { mutableStateOf<List<GoalRow>?>(null) }
    var flash by remember { mutableStateOf<String?>(null) }
    var confirm by remember { mutableStateOf<Confirm?>(null) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(connected, head?.goalLinks) {
        if (!connected) return@LaunchedEffect
        goals = withContext(Dispatchers.IO) { partner.goals(head?.goalLinks ?: emptyMap()) }
    }
    // Opening Work counts as having seen "while you were away".
    LaunchedEffect(connected, head?.activityUnseen) {
        if (connected && (head?.activityUnseen ?: 0) > 0) {
            withContext(Dispatchers.IO) { partner.markSeen() }
        }
    }
    LaunchedEffect(flash) {
        if (flash != null) {
            delay(4000)
            flash = null
        }
    }

    fun act(run: () -> ApiResult, ok: String) {
        scope.launch {
            val r = withContext(Dispatchers.IO) { run() }
            flash = if (r.ok) {
                val note = r.json?.optString("capacity_note")?.takeIf { it.isNotBlank() }
                if (note != null) "$ok $note" else ok
            } else {
                r.problem()
            }
            onChanged()
        }
    }

    confirm?.let { c ->
        AlertDialog(
            onDismissRequest = { confirm = null },
            title = { Text(c.title) },
            text = { Text(c.body, fontSize = 15.sp, lineHeight = 21.sp) },
            confirmButton = {
                TextButton(onClick = {
                    confirm = null
                    act(c.run, "Done.")
                }) { Text(c.yes, color = Accent, fontSize = 16.sp) }
            },
            dismissButton = { TextButton(onClick = { confirm = null }) { Text("Cancel", fontSize = 16.sp) } },
        )
    }

    val h = head
    LazyColumn(
        Modifier.fillMaxSize(),
        contentPadding = PaddingValues(horizontal = 14.dp, vertical = 8.dp),
        verticalArrangement = Arrangement.spacedBy(10.dp),
    ) {
        flash?.let { f ->
            item {
                Text(
                    f,
                    color = TextSecondary,
                    fontSize = 14.sp,
                    modifier = Modifier.semantics { liveRegion = LiveRegionMode.Polite },
                )
            }
        }
        if (h == null) {
            item {
                Box(Modifier.fillMaxWidth().padding(32.dp), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = Accent, strokeWidth = 3.dp)
                }
            }
            return@LazyColumn
        }
        item {
            WorkCard {
                Text(
                    if (h.headState == "working") "Remedy is working" else "Remedy is resting",
                    color = TextPrimary,
                    fontSize = 18.sp,
                    fontWeight = FontWeight.SemiBold,
                )
                if (h.capacityNote.isNotBlank()) {
                    Spacer(Modifier.height(4.dp))
                    Text(h.capacityNote, color = TextMuted, fontSize = 13.sp, lineHeight = 18.sp)
                }
                if (h.heartbeatOn) {
                    Spacer(Modifier.height(4.dp))
                    Text(
                        "Keeping watch" + (h.heartbeatNote?.let { " — $it" } ?: ""),
                        color = TextSecondary,
                        fontSize = 13.sp,
                    )
                }
            }
        }

        item { SectionTitle("Side jobs", if (h.children.isEmpty()) "None right now" else null) }
        items(h.children, key = { "c-" + it.id }) { c ->
            WorkCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    StateDot(c.state)
                    Spacer(Modifier.width(8.dp))
                    Text(
                        c.label,
                        color = TextPrimary,
                        fontSize = 16.sp,
                        fontWeight = FontWeight.Medium,
                        modifier = Modifier.weight(1f),
                        maxLines = 2,
                        overflow = TextOverflow.Ellipsis,
                    )
                    Text(stateWords(c.state), color = if (needsOwner(c.state)) Warning else TextMuted, fontSize = 13.sp)
                }
                c.lastReport?.let {
                    Spacer(Modifier.height(4.dp))
                    Text(it, color = TextSecondary, fontSize = 14.sp, maxLines = 4, overflow = TextOverflow.Ellipsis)
                }
                if (canStop(c.state)) {
                    Spacer(Modifier.height(6.dp))
                    OutlinedButton(
                        onClick = {
                            confirm = Confirm(
                                "Stop this side job?",
                                "Remedy will stop “${c.label}” on your PC. What it already did stays.",
                                "Stop it",
                            ) { partner.stopChild(c.id) }
                        },
                        enabled = connected,
                        border = BorderStroke(1.dp, Error.copy(alpha = 0.6f)),
                        colors = ButtonDefaults.outlinedButtonColors(contentColor = Error),
                        shape = RoundedCornerShape(12.dp),
                        modifier = Modifier.height(44.dp),
                    ) { Text("Stop", fontSize = 15.sp) }
                }
            }
        }

        val gs = goals.orEmpty().filter { it.status != "archived" }
        item { SectionTitle("Goals", if (goals != null && gs.isEmpty()) "No goals yet — tell Remedy one in Talk" else null) }
        items(gs, key = { "g-" + it.id }) { g ->
            WorkCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            g.title,
                            color = TextPrimary,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Medium,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                        val sub = listOfNotNull(
                            if (g.status == "done") "Done" else null,
                            if (g.autopilot) "On autopilot · ${stateWords(g.state).lowercase()}" else null,
                            g.nextAction?.let { "Next: $it" },
                        ).joinToString(" · ")
                        if (sub.isNotBlank()) {
                            Text(sub, color = TextMuted, fontSize = 13.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
                        }
                        g.lastUpdate?.let {
                            Text(it, color = TextSecondary, fontSize = 13.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
                        }
                    }
                    if (g.status != "done") {
                        Spacer(Modifier.width(8.dp))
                        Switch(
                            checked = g.autopilot,
                            enabled = connected,
                            onCheckedChange = { on ->
                                if (on) {
                                    confirm = Confirm(
                                        "Let Remedy work on this goal by herself?",
                                        "She'll take small steps on your PC from time to time. Anything involving money, passwords or sending still asks you first.",
                                        "Turn on",
                                    ) { partner.setAutopilot(g.id, true) }
                                } else {
                                    act({ partner.setAutopilot(g.id, false) }, "Autopilot off.")
                                }
                            },
                            colors = SwitchDefaults.colors(checkedTrackColor = Accent),
                        )
                    }
                }
            }
        }

        item {
            SectionTitle(
                "Routines",
                if (h.routines.isEmpty()) "None yet — ask Remedy in Talk (“remind me every morning…”)" else null,
            )
        }
        items(h.routines, key = { "r-" + it.id }) { r ->
            WorkCard {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f)) {
                        Text(
                            r.title,
                            color = TextPrimary,
                            fontSize = 16.sp,
                            fontWeight = FontWeight.Medium,
                            maxLines = 2,
                            overflow = TextOverflow.Ellipsis,
                        )
                        val next = if (r.enabled) {
                            relativeWhen(r.nextRun).takeIf { it.isNotBlank() }?.let { "next $it" }
                        } else {
                            "paused"
                        }
                        Text(
                            listOfNotNull(r.whenText.ifBlank { null }, next).joinToString(" · "),
                            color = TextMuted,
                            fontSize = 13.sp,
                        )
                        r.lastSummary?.let {
                            Text(it, color = TextSecondary, fontSize = 13.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                        }
                    }
                    Switch(
                        checked = r.enabled,
                        enabled = connected,
                        onCheckedChange = { on ->
                            if (on) {
                                confirm = Confirm(
                                    "Turn this routine back on?",
                                    "“${r.title}” will run on its schedule on your PC again.",
                                    "Turn on",
                                ) { partner.setRoutineEnabled(r.id, true) }
                            } else {
                                act({ partner.setRoutineEnabled(r.id, false) }, "Routine paused.")
                            }
                        },
                        colors = SwitchDefaults.colors(checkedTrackColor = Accent),
                    )
                }
                TextButton(
                    onClick = {
                        confirm = Confirm(
                            "Run it now?",
                            "Remedy will run “${r.title}” on your PC now.",
                            "Run now",
                        ) { partner.runRoutine(r.id) }
                    },
                    enabled = connected,
                ) { Text("Run now", color = Accent, fontSize = 15.sp) }
            }
        }

        item { SectionTitle("While you were away", if (h.activity.isEmpty()) "Nothing new" else null) }
        items(h.activity.take(30), key = { "a-" + it.id.ifBlank { it.ts + it.title } }) { a ->
            Row(Modifier.fillMaxWidth().padding(vertical = 4.dp)) {
                Text(
                    relativeWhen(a.ts).ifBlank { " " },
                    color = TextMuted,
                    fontSize = 12.sp,
                    modifier = Modifier.width(68.dp),
                )
                Column(Modifier.weight(1f)) {
                    Text(a.title, color = TextPrimary, fontSize = 15.sp)
                    a.detail?.let {
                        Text(it, color = TextSecondary, fontSize = 13.sp, maxLines = 3, overflow = TextOverflow.Ellipsis)
                    }
                }
            }
        }
        item { Spacer(Modifier.height(12.dp)) }
    }
}

@Composable
private fun SectionTitle(title: String, empty: String?) {
    Column(Modifier.padding(top = 8.dp)) {
        Text(
            title,
            color = TextPrimary,
            fontSize = 17.sp,
            fontWeight = FontWeight.SemiBold,
            modifier = Modifier.semantics { heading() },
        )
        empty?.let { Text(it, color = TextMuted, fontSize = 14.sp) }
    }
}

@Composable
private fun WorkCard(content: @Composable () -> Unit) {
    Card(
        Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = BgSecondary),
        border = BorderStroke(1.dp, Border),
    ) {
        Column(Modifier.padding(14.dp)) { content() }
    }
}

@Composable
private fun StateDot(state: String) {
    val c = when {
        needsOwner(state) -> Warning
        state == "working" || state == "queued" -> Accent
        state == "done" -> Success
        else -> TextMuted
    }
    Box(Modifier.size(10.dp).background(c, CircleShape))
}
