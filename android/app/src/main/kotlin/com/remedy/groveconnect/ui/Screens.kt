package com.remedy.groveconnect.ui

import android.annotation.SuppressLint
import android.view.ViewGroup
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.compose.foundation.background
import androidx.compose.foundation.border
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
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
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
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.viewinterop.AndroidView
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

@Composable
fun LockScreen(onUnlock: () -> Unit, error: String?) {
    Column(
        Modifier.fillMaxSize().background(BgPrimary).padding(28.dp),
        verticalArrangement = Arrangement.Center,
        horizontalAlignment = Alignment.CenterHorizontally,
    ) {
        Text("Grove Connect", color = TextPrimary, fontSize = 28.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        Text("Unlock to open the remote.", color = TextSecondary, fontSize = 16.sp)
        Spacer(Modifier.height(28.dp))
        Button(
            onClick = onUnlock,
            colors = ButtonDefaults.buttonColors(containerColor = Accent),
            modifier = Modifier.fillMaxWidth().height(52.dp),
            shape = RoundedCornerShape(14.dp),
        ) {
            Text("Unlock", fontSize = 17.sp)
        }
        if (!error.isNullOrBlank()) {
            Spacer(Modifier.height(16.dp))
            Text(error, color = Error)
        }
    }
}

@Composable
fun PairScreen(
    state: RemoteState,
    onPair: (String) -> Unit,
    onUnpair: () -> Unit,
) {
    var text by remember { mutableStateOf("") }
    Column(
        Modifier.fillMaxSize().background(BgPrimary).padding(22.dp).verticalScroll(rememberScrollState()),
    ) {
        Text("Pair with your PC", color = TextPrimary, fontSize = 24.sp, fontWeight = FontWeight.SemiBold)
        Spacer(Modifier.height(8.dp))
        Text(
            "Paste the pairing code from Remedy. Camera comes later — paste is enough.",
            color = TextSecondary,
            fontSize = 15.sp,
        )
        Spacer(Modifier.height(16.dp))
        TextField(
            value = text,
            onValueChange = { text = it },
            modifier = Modifier.fillMaxWidth().height(180.dp),
            placeholder = { Text("remedy-connect/1\nhp=…", color = TextMuted) },
            colors = TextFieldDefaults.colors(
                focusedContainerColor = BgSecondary,
                unfocusedContainerColor = BgSecondary,
                focusedTextColor = TextPrimary,
                unfocusedTextColor = TextPrimary,
                cursorColor = Accent,
            ),
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = { onPair(text) },
            enabled = text.contains("hp="),
            colors = ButtonDefaults.buttonColors(containerColor = Accent),
            modifier = Modifier.fillMaxWidth().height(52.dp),
            shape = RoundedCornerShape(14.dp),
        ) { Text("Pair") }
        if (state.paired) {
            Text(
                "This phone is pinned to a PC" + (state.lanLabel?.let { " ($it)" } ?: "") +
                    ". Paste a fresh code from that same PC. A different PC key is rejected.",
                color = TextSecondary,
                fontSize = 13.sp,
            )
            TextButton(onClick = onUnpair) { Text("Unpair this phone", color = TextSecondary) }
        }
        if (!state.error.isNullOrBlank()) {
            Spacer(Modifier.height(12.dp))
            Text(state.error, color = Error)
        }
    }
}

@Composable
fun RemoteScreen(
    state: RemoteState,
    onStop: () -> Unit,
    onClose: () -> Unit,
    onResolve: (String, Boolean) -> Unit,
    onPairAnother: () -> Unit,
) {
    Column(Modifier.fillMaxSize().background(BgPrimary)) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 12.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("Grove Connect", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
            Spacer(Modifier.weight(1f))
            ReachableChip(state.reachable)
        }
        Button(
            onClick = onStop,
            colors = ButtonDefaults.buttonColors(containerColor = Error),
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp).height(64.dp),
            shape = RoundedCornerShape(16.dp),
        ) {
            Text("Stop", fontSize = 22.sp, fontWeight = FontWeight.Bold)
        }
        if (!state.error.isNullOrBlank()) {
            Text(state.error!!, color = Error, modifier = Modifier.padding(16.dp, 8.dp))
        }
        if (state.approvals.isNotEmpty()) {
            ApprovalsBlock(state.approvals, onResolve)
        }
        PaneRow(state.panes)
        val url = state.shimUrl
        if (url != null && (state.reachable == Reachable.OnLan || state.reachable == Reachable.OnRelay)) {
            Box(Modifier.weight(1f).padding(8.dp).clip(RoundedCornerShape(12.dp)).background(BgSecondary)) {
                ConnectWebView(url)
            }
        } else {
            Box(Modifier.weight(1f), contentAlignment = Alignment.Center) {
                Text(
                    if (state.reachable == Reachable.Connecting) "Connecting…" else "Remote paused. Pair again to reopen.",
                    color = TextSecondary,
                )
            }
        }
        Row(Modifier.fillMaxWidth().padding(8.dp), horizontalArrangement = Arrangement.SpaceBetween) {
            TextButton(onClick = onPairAnother) { Text("New pair", color = TextSecondary) }
            TextButton(onClick = onClose) { Text("Close remote", color = TextSecondary) }
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
        Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp),
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
    Column(
        Modifier.fillMaxWidth().padding(16.dp).clip(RoundedCornerShape(12.dp)).background(BgTertiary).padding(14.dp),
    ) {
        Text("Remedy is waiting", color = Warning, fontWeight = FontWeight.SemiBold)
        for (item in items) {
            Spacer(Modifier.height(8.dp))
            Text(item.summary, color = TextPrimary, fontSize = 16.sp)
            if (item.sensitive) {
                Text("This always asks — money, secrets, or send.", color = TextMuted, fontSize = 13.sp)
            }
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { onResolve(item.id, true) },
                    colors = ButtonDefaults.buttonColors(containerColor = Accent),
                ) { Text("Yes") }
                Button(
                    onClick = { onResolve(item.id, false) },
                    colors = ButtonDefaults.buttonColors(containerColor = BgSecondary),
                ) { Text("No") }
            }
        }
    }
}

@SuppressLint("SetJavaScriptEnabled")
@Composable
private fun ConnectWebView(url: String) {
    AndroidView(
        modifier = Modifier.fillMaxSize(),
        factory = { ctx ->
            WebView(ctx).apply {
                layoutParams = ViewGroup.LayoutParams(
                    ViewGroup.LayoutParams.MATCH_PARENT,
                    ViewGroup.LayoutParams.MATCH_PARENT,
                )
                settings.javaScriptEnabled = true
                settings.domStorageEnabled = true
                settings.allowFileAccess = false
                settings.allowContentAccess = false
                webViewClient = object : WebViewClient() {
                    override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                        val host = request.url.host
                        return host != "127.0.0.1" && host != "localhost"
                    }
                }
                loadUrl(url)
            }
        },
        update = { view ->
            if (view.url != url) view.loadUrl(url)
        },
    )
}
