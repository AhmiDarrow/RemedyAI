package com.remedy.groveconnect.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.Icon
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.material3.TextField
import androidx.compose.material3.TextFieldDefaults
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.remedy.groveconnect.connect.ApprovalItem
import com.remedy.groveconnect.connect.Reachable
import com.remedy.groveconnect.connect.RemoteState
import com.remedy.groveconnect.core.PaneFlags
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

/** Small QR-style brand glyph drawn locally — no icon dependency. */
@Composable
private fun BrandMark(size: Dp = 84.dp) {
    val d = with(LocalDensity.current) { size.toPx() }
    Canvas(Modifier.size(size)) {
        val stroke = d * 0.05f
        val finder = d * 0.44f
        val off = d * 0.11f
        val inner = finder * 0.5f
        val r = CornerRadius(d * 0.06f)
        // three finder corners
        drawRoundRect(Accent, topLeft = Offset(off, off), size = Size(finder, finder), style = Stroke(stroke), cornerRadius = r)
        drawRoundRect(
            Accent,
            topLeft = Offset(off + finder / 2 - inner / 2, off + finder / 2 - inner / 2),
            size = Size(inner, inner),
            cornerRadius = r,
        )
        drawRoundRect(Accent, topLeft = Offset(d - off - finder, off), size = Size(finder, finder), style = Stroke(stroke), cornerRadius = r)
        drawRoundRect(
            Accent,
            topLeft = Offset(d - off - finder + finder / 2 - inner / 2, off + finder / 2 - inner / 2),
            size = Size(inner, inner),
            cornerRadius = r,
        )
        drawRoundRect(Accent, topLeft = Offset(off, d - off - finder), size = Size(finder, finder), style = Stroke(stroke), cornerRadius = r)
        drawRoundRect(
            Accent,
            topLeft = Offset(off + finder / 2 - inner / 2, d - off - finder + finder / 2 - inner / 2),
            size = Size(inner, inner),
            cornerRadius = r,
        )
        // data dots
        val dot = d * 0.04f
        drawCircle(Accent, radius = dot, center = Offset(d * 0.60f, d * 0.60f))
        drawCircle(Accent, radius = dot, center = Offset(d * 0.71f, d * 0.69f))
        drawCircle(Accent, radius = dot, center = Offset(d * 0.57f, d * 0.73f))
    }
}

@Composable
fun LockScreen(onUnlock: () -> Unit, error: String?) {
    Column(
        Modifier.fillMaxSize().background(BgPrimary).padding(28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        BrandMark(size = 84.dp)
        Spacer(Modifier.height(24.dp))
        Text("RemedyConnect", color = TextPrimary, fontSize = 28.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        Text(
            "Remedy on your PC, in your pocket.",
            color = TextSecondary,
            fontSize = 15.sp,
            textAlign = TextAlign.Center,
        )
        Spacer(Modifier.height(32.dp))
        Button(
            onClick = onUnlock,
            colors = ButtonDefaults.buttonColors(containerColor = Accent),
            modifier = Modifier.fillMaxWidth().height(56.dp),
            shape = RoundedCornerShape(18.dp),
        ) {
            Icon(Icons.Filled.Lock, contentDescription = null)
            Spacer(Modifier.width(8.dp))
            Text("Unlock", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
        }
        if (!error.isNullOrBlank()) {
            Spacer(Modifier.height(18.dp))
            Text(error, color = Error, fontSize = 13.sp, textAlign = TextAlign.Center)
        }
    }
}

@Composable
private fun SegmentTab(label: String, selected: Boolean, modifier: Modifier = Modifier, onClick: () -> Unit) {
    Box(
        modifier
            .clip(RoundedCornerShape(11.dp))
            .background(if (selected) Accent else BgSecondary)
            .clickable(onClick = onClick)
            .padding(vertical = 10.dp),
        contentAlignment = Alignment.Center,
    ) {
        Text(
            label,
            color = if (selected) Color.White else TextSecondary,
            fontSize = 14.sp,
            fontWeight = if (selected) FontWeight.SemiBold else FontWeight.Normal,
        )
    }
}

@Composable
fun PairScreen(
    state: RemoteState,
    onPair: (String) -> Unit,
    onUnpair: () -> Unit,
) {
    var text by remember { mutableStateOf("") }
    var scanMode by remember { mutableStateOf(true) }
    var scanError by remember { mutableStateOf<String?>(null) }
    Column(
        Modifier.fillMaxSize().background(BgPrimary).padding(22.dp).verticalScroll(rememberScrollState()),
    ) {
        Text("Pair with your PC", color = TextPrimary, fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(6.dp))
        Text("Scan the QR in Remedy → Settings → Connect.", color = TextSecondary, fontSize = 15.sp)
        Spacer(Modifier.height(18.dp))
        Row(
            Modifier.fillMaxWidth().clip(RoundedCornerShape(14.dp)).background(BgSecondary).padding(4.dp),
            horizontalArrangement = Arrangement.spacedBy(4.dp),
        ) {
            SegmentTab("Scan QR", selected = scanMode, modifier = Modifier.weight(1f)) { scanMode = true }
            SegmentTab("Paste code", selected = !scanMode, modifier = Modifier.weight(1f)) { scanMode = false }
        }
        Spacer(Modifier.height(18.dp))
        if (scanMode) {
            QrScanBox(
                onScanned = { raw -> onPair(raw) },
                onError = { err -> scanError = err },
                // A failed pair re-arms the scanner so the next QR is picked up.
                rearmKey = state.error,
            )
            Spacer(Modifier.height(10.dp))
            Text(
                "Hold the camera steady over the QR — it pairs instantly.",
                color = TextMuted,
                fontSize = 13.sp,
            )
            if (!scanError.isNullOrBlank()) {
                Spacer(Modifier.height(6.dp))
                Text(scanError ?: "", color = Error, fontSize = 13.sp)
            }
        } else {
            TextField(
                value = text,
                onValueChange = { text = it },
                modifier = Modifier.fillMaxWidth().height(150.dp),
                placeholder = { Text("remedy-connect/1\nhp=…", color = TextMuted) },
                colors = TextFieldDefaults.colors(
                    focusedContainerColor = BgSecondary,
                    unfocusedContainerColor = BgSecondary,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary,
                    cursorColor = Accent,
                ),
            )
            Spacer(Modifier.height(14.dp))
            Button(
                onClick = { onPair(text.trim()) },
                enabled = text.isNotBlank(),
                colors = ButtonDefaults.buttonColors(containerColor = Accent),
                modifier = Modifier.fillMaxWidth().height(54.dp),
                shape = RoundedCornerShape(16.dp),
            ) {
                Text("Pair", fontSize = 17.sp, fontWeight = FontWeight.SemiBold)
            }
        }
        if (state.paired) {
            Spacer(Modifier.height(16.dp))
            Row(
                Modifier.fillMaxWidth().clip(RoundedCornerShape(12.dp)).background(BgSecondary).padding(12.dp),
                verticalAlignment = Alignment.CenterVertically,
            ) {
                Box(Modifier.size(8.dp).clip(CircleShape).background(Success))
                Spacer(Modifier.width(8.dp))
                Text(
                    "Pinned to a PC" + (state.lanLabel?.let { " · $it" } ?: ""),
                    color = TextSecondary,
                    fontSize = 13.sp,
                    modifier = Modifier.weight(1f),
                )
                TextButton(onClick = onUnpair) { Text("Unpair", color = TextSecondary) }
            }
        }
        if (!state.error.isNullOrBlank()) {
            Spacer(Modifier.height(12.dp))
            Text(state.error ?: "", color = Error, fontSize = 13.sp)
        }
    }
}

@Composable
fun HubScreen(
    state: RemoteState,
    onStop: () -> Unit,
    onClose: () -> Unit,
    onResolve: (String, Boolean) -> Unit,
    onPairAnother: () -> Unit,
    onOpenFullRemote: () -> Unit,
) {
    Column(
        Modifier.fillMaxSize().background(BgPrimary).padding(horizontal = 20.dp, vertical = 16.dp),
    ) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            BrandMark(size = 30.dp)
            Spacer(Modifier.width(10.dp))
            Text("RemedyConnect", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 20.sp)
            Spacer(Modifier.weight(1f))
            ReachableChip(state.reachable)
        }
        Spacer(Modifier.height(20.dp))

        Column(Modifier.weight(1f).verticalScroll(rememberScrollState())) {
            HeroCard(state, onOpenFullRemote, onStop)
            Spacer(Modifier.height(18.dp))
            PaneRow(state.panes)
            Spacer(Modifier.height(18.dp))
            if (state.approvals.isNotEmpty()) {
                ApprovalsBlock(state.approvals, onResolve)
            } else {
                Text(
                    "Nothing needs you right now.",
                    color = TextMuted,
                    fontSize = 14.sp,
                    modifier = Modifier.fillMaxWidth(),
                    textAlign = TextAlign.Center,
                )
            }
        }

        Spacer(Modifier.height(12.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            TextButton(onClick = onPairAnother) { Text("New pair", color = TextSecondary) }
            TextButton(onClick = onClose) { Text("Close remote", color = TextSecondary) }
        }
    }
}

@Composable
private fun HeroCard(
    state: RemoteState,
    onOpenFullRemote: () -> Unit,
    onStop: () -> Unit,
) {
    val (statusLabel, statusColor) = when (state.reachable) {
        Reachable.Connecting -> "Connecting" to Warning
        Reachable.OnLan -> "On your Wi-Fi" to Success
        Reachable.OnRelay -> "Via relay" to Success
        Reachable.Paused -> "Paused" to TextMuted
    }
    Card(
        Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(20.dp),
        colors = CardDefaults.cardColors(containerColor = BgSecondary),
        border = BorderStroke(1.dp, Border),
    ) {
        Column(Modifier.padding(20.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(10.dp).clip(CircleShape).background(statusColor))
                Spacer(Modifier.width(10.dp))
                Text(statusLabel, color = TextPrimary, fontSize = 22.sp, fontWeight = FontWeight.Bold)
            }
            Spacer(Modifier.height(6.dp))
            state.lanLabel?.let {
                Text(it, color = TextSecondary, fontSize = 14.sp)
            }
            state.sessionId?.let { sid ->
                if (sid.length >= 12) {
                    Text("session ${sid.take(8)}…", color = TextMuted, fontSize = 13.sp)
                }
            }
            Spacer(Modifier.height(18.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                Button(
                    onClick = onOpenFullRemote,
                    colors = ButtonDefaults.buttonColors(containerColor = Accent),
                    modifier = Modifier.weight(1f).height(52.dp),
                    shape = RoundedCornerShape(16.dp),
                ) {
                    Icon(Icons.Filled.PlayArrow, contentDescription = null)
                    Spacer(Modifier.width(6.dp))
                    Text("Full remote", fontSize = 16.sp, fontWeight = FontWeight.SemiBold)
                }
                OutlinedButton(
                    onClick = onStop,
                    modifier = Modifier.height(52.dp),
                    shape = RoundedCornerShape(16.dp),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = Error),
                    border = BorderStroke(1.dp, Error.copy(alpha = 0.5f)),
                ) {
                    Text("Stop", fontSize = 16.sp)
                }
            }
        }
    }
}

@Composable
private fun ReachableChip(r: Reachable) {
    val (label, color) = when (r) {
        Reachable.Connecting -> "connecting" to Warning
        Reachable.OnLan -> "on LAN" to Success
        Reachable.OnRelay -> "via relay" to Success
        Reachable.Paused -> "paused" to TextMuted
    }
    Row(
        Modifier.clip(RoundedCornerShape(20.dp)).background(BgTertiary).padding(horizontal = 12.dp, vertical = 6.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Box(Modifier.size(8.dp).clip(CircleShape).background(color))
        Text("  $label", color = TextPrimary, fontSize = 13.sp)
    }
}

@Composable
private fun PaneRow(panes: PaneFlags) {
    val vis = panes.visible()
    if (vis.isEmpty()) return
    Row(
        Modifier.fillMaxWidth().padding(horizontal = 4.dp, vertical = 4.dp),
        horizontalArrangement = Arrangement.spacedBy(6.dp),
    ) {
        for (id in vis) {
            Text(
                id,
                color = TextSecondary,
                fontSize = 12.sp,
                modifier = Modifier
                    .clip(RoundedCornerShape(8.dp))
                    .background(BgTertiary)
                    .border(1.dp, Border, RoundedCornerShape(8.dp))
                    .padding(horizontal = 8.dp, vertical = 4.dp),
            )
        }
    }
}

@Composable
private fun ApprovalsBlock(items: List<ApprovalItem>, onResolve: (String, Boolean) -> Unit) {
    Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Box(Modifier.size(8.dp).clip(CircleShape).background(Warning))
            Spacer(Modifier.width(8.dp))
            Text("Remedy needs you", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 16.sp)
            Spacer(Modifier.width(8.dp))
            Box(
                Modifier.clip(RoundedCornerShape(10.dp)).background(BgTertiary)
                    .padding(horizontal = 8.dp, vertical = 2.dp),
            ) {
                Text("${items.size}", color = TextSecondary, fontSize = 12.sp)
            }
        }
        for (item in items) {
            Card(
                Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = BgSecondary),
                border = BorderStroke(1.dp, Border),
            ) {
                Column(Modifier.padding(16.dp)) {
                    Text(item.summary, color = TextPrimary, fontSize = 15.sp)
                    if (item.sensitive) {
                        Spacer(Modifier.height(4.dp))
                        Text("Always asks — money, secrets, or send.", color = TextMuted, fontSize = 12.sp)
                    }
                    Spacer(Modifier.height(12.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                        Button(
                            onClick = { onResolve(item.id, true) },
                            colors = ButtonDefaults.buttonColors(containerColor = Accent),
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.height(42.dp),
                        ) {
                            Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Approve")
                        }
                        OutlinedButton(
                            onClick = { onResolve(item.id, false) },
                            shape = RoundedCornerShape(12.dp),
                            modifier = Modifier.height(42.dp),
                            colors = ButtonDefaults.outlinedButtonColors(contentColor = TextSecondary),
                            border = BorderStroke(1.dp, Border),
                        ) {
                            Icon(Icons.Filled.Clear, contentDescription = null, modifier = Modifier.size(18.dp))
                            Spacer(Modifier.width(4.dp))
                            Text("Decline")
                        }
                    }
                }
            }
        }
    }
}
