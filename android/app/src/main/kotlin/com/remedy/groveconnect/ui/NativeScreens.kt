package com.remedy.groveconnect.ui

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
import androidx.compose.material.icons.filled.Check
import androidx.compose.material.icons.filled.Clear
import androidx.compose.material.icons.filled.Close
import androidx.compose.material.icons.filled.Refresh
import androidx.compose.material.icons.filled.Warning
import androidx.compose.material.icons.filled.Info
import androidx.compose.material.icons.filled.List
import androidx.compose.material.icons.filled.Menu
import androidx.compose.material.icons.filled.Lock
import androidx.compose.material.icons.filled.PlayArrow
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material.icons.filled.Terminal
import androidx.compose.material.icons.filled.Chat
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.NavigationBar
import androidx.compose.material3.NavigationBarItem
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Surface
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
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.remedy.groveconnect.api.Approval
import com.remedy.groveconnect.api.ChatMessage
import com.remedy.groveconnect.api.ChatSession
import com.remedy.groveconnect.api.RemedyApi
import com.remedy.groveconnect.api.SessionsApi
import com.remedy.groveconnect.api.TerminalApi
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
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** Tab ids for the native home. */
enum class HomeTab(val label: String) {
    Chat("Chat"),
    Sessions("Sessions"),
    Approvals("Approvals"),
    Terminal("Terminal"),
    Grove("Grove"),
    Settings("Settings"),
}

/**
 * Native Grove Connect home. Replaces the desktop-WebView "Full remote" with
 * a phone-first UI that talks directly to the PC API through the tunnel.
 */
@Composable
fun HomeScreen(
    state: RemoteState,
    api: RemedyApi,
    onClose: () -> Unit,
    onStop: () -> Unit,
    onRefresh: () -> Unit,
) {
    var tab by remember { mutableStateOf(HomeTab.Chat) }
    var pendingSessionId by remember { mutableStateOf<String?>(null) }
    val sessionsApi = remember { SessionsApi(api) }
    val terminalApi = remember { TerminalApi(api) }
    var preferredModel by remember { mutableStateOf<String?>(null) }

    Column(Modifier.fillMaxSize().background(BgPrimary).statusBarsPadding()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 10.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text("RemedyConnect", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 19.sp)
            Spacer(Modifier.weight(1f))
            StatusDot(state.reachable)
            Spacer(Modifier.width(8.dp))
            IconButton(onClick = onRefresh) {
                Icon(Icons.Filled.Refresh, contentDescription = "Refresh", tint = TextSecondary, modifier = Modifier.size(20.dp))
            }
            IconButton(onClick = onClose) {
                Icon(Icons.Filled.Close, contentDescription = "Close", tint = TextSecondary, modifier = Modifier.size(20.dp))
            }
        }
        ProviderModelBar(api, preferredModel, onModelPicked = { preferredModel = it })
        Box(Modifier.weight(1f)) {
            when (tab) {
                HomeTab.Chat -> ChatTab(
                    sessionsApi, api, state, onStop, preferredModel,
                    initialSessionId = pendingSessionId,
                    onInitialConsumed = { pendingSessionId = null },
                )
                HomeTab.Sessions -> SessionsTab(
                    sessionsApi,
                    onOpen = { s ->
                        pendingSessionId = s.id
                        tab = HomeTab.Chat
                    },
                )
                HomeTab.Approvals -> ApprovalsTab(sessionsApi)
                HomeTab.Terminal -> TerminalTab(terminalApi)
                HomeTab.Grove -> GroveTab(api)
                HomeTab.Settings -> SettingsTab(api, state, onStop, onClose, onModelPicked = { preferredModel = it })
            }
        }
        NavigationBar(containerColor = BgSecondary, contentColor = TextPrimary) {
            HomeTab.entries.forEach { t ->
                NavigationBarItem(
                    selected = tab == t,
                    onClick = { tab = t },
                    icon = { Icon(HomeTabIcon(t), contentDescription = t.label, modifier = Modifier.size(22.dp)) },
                    label = { Text(t.label, fontSize = 11.sp) },
                    colors = androidx.compose.material3.NavigationBarItemDefaults.colors(
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

@Composable
private fun HomeTabIcon(tab: HomeTab) = when (tab) {
    HomeTab.Chat -> Icons.Filled.Chat
    HomeTab.Sessions -> Icons.Filled.List
    HomeTab.Approvals -> Icons.Filled.Warning
    HomeTab.Terminal -> Icons.Filled.Terminal
    HomeTab.Grove -> Icons.Filled.Menu
    HomeTab.Settings -> Icons.Filled.Info
}

@Composable
private fun StatusDot(r: Reachable) {
    val color = when (r) {
        Reachable.Connecting -> Warning
        Reachable.OnLan, Reachable.OnRelay -> Success
        Reachable.Paused -> TextMuted
    }
    Box(Modifier.size(9.dp).background(color, CircleShape))
}

/**
 * Slim, always-visible provider·model status bar under the title. Shows the
 * PC's live LLM provider/model and lets the phone switch either in-session
 * without opening Settings. Provider/model writes are allowed regardless of
 * the settings_write pane (see connect/deny.py _PROVIDER_SAFE_KEYS).
 */
@Composable
private fun ProviderModelBar(
    api: RemedyApi,
    preferredModel: String?,
    onModelPicked: (String?) -> Unit,
) {
    val sessionsApi = remember { SessionsApi(api) }
    val scope = rememberCoroutineScope()
    var providers by remember { mutableStateOf<List<Pair<String, String>>?>(null) }
    var models by remember { mutableStateOf<List<Pair<String, String>>?>(null) }
    var currentProvider by remember { mutableStateOf<String?>(null) }
    var currentModel by remember { mutableStateOf<String?>(null) }
    var open by remember { mutableStateOf(false) }
    var refreshKey by remember { mutableIntStateOf(0) }

    LaunchedEffect(refreshKey) {
        val snap = withContext(Dispatchers.IO) { SettingsSnapshot.load(api) }
        providers = snap.providers
        models = snap.models
        currentProvider = snap.currentProvider
        currentModel = snap.currentModel
    }

    val provs = providers
    // A model picked in this session (or from Settings) wins over the last
    // fetched value so the label updates immediately.
    val shownModel = preferredModel ?: currentModel
    val provLabel = provs?.firstOrNull { it.first == currentProvider }?.second ?: currentProvider
    val hasProviders = provs == null || provs.isNotEmpty()
    val label = when {
        provs != null && provs.isEmpty() -> "No providers connected"
        provLabel == null -> "Loading…"
        !shownModel.isNullOrBlank() -> "$provLabel · $shownModel"
        else -> "$provLabel · Default model"
    }

    Box {
        Row(
            Modifier
                .fillMaxWidth()
                .height(38.dp)
                .background(BgSecondary)
                .clickable(enabled = hasProviders) { open = true }
                .padding(horizontal = 16.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Text(
                label,
                color = if (hasProviders) TextSecondary else TextMuted,
                fontSize = 13.sp,
                maxLines = 1,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.weight(1f),
            )
            if (hasProviders) {
                Spacer(Modifier.width(8.dp))
                Text("▾", color = TextMuted, fontSize = 13.sp)
            }
        }
        DropdownMenu(
            expanded = open,
            onDismissRequest = { open = false },
            modifier = Modifier.background(BgSecondary),
        ) {
            Text(
                "Provider",
                color = TextMuted,
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(start = 16.dp, top = 6.dp, bottom = 2.dp),
            )
            provs?.forEach { (id, plabel) ->
                val sel = id == currentProvider
                DropdownMenuItem(
                    text = { Text(plabel, color = if (sel) Accent else TextPrimary, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                    trailingIcon = { if (sel) Icon(Icons.Filled.Check, contentDescription = null, tint = Accent, modifier = Modifier.size(16.dp)) },
                    onClick = {
                        // Switch provider, reset to its default model, keep the
                        // menu open so a model can be chosen next.
                        scope.launch {
                            withContext(Dispatchers.IO) { sessionsApi.setProvider(id, null) }
                            onModelPicked(null)
                            refreshKey++
                        }
                    },
                )
            }
            HorizontalDivider(color = Border)
            Text(
                "Model",
                color = TextMuted,
                fontSize = 11.sp,
                fontWeight = FontWeight.SemiBold,
                modifier = Modifier.padding(start = 16.dp, top = 6.dp, bottom = 2.dp),
            )
            DropdownMenuItem(
                text = { Text("Provider default", color = if (shownModel.isNullOrBlank()) Accent else TextPrimary, fontSize = 14.sp) },
                trailingIcon = { if (shownModel.isNullOrBlank()) Icon(Icons.Filled.Check, contentDescription = null, tint = Accent, modifier = Modifier.size(16.dp)) },
                onClick = {
                    open = false
                    scope.launch {
                        withContext(Dispatchers.IO) { sessionsApi.resetModel(currentProvider) }
                        onModelPicked(null)
                        refreshKey++
                    }
                },
            )
            models?.take(12)?.forEach { (mid, mlabel) ->
                val sel = mid == shownModel
                DropdownMenuItem(
                    text = { Text(mlabel, color = if (sel) Accent else TextPrimary, fontSize = 14.sp, maxLines = 1, overflow = TextOverflow.Ellipsis) },
                    trailingIcon = { if (sel) Icon(Icons.Filled.Check, contentDescription = null, tint = Accent, modifier = Modifier.size(16.dp)) },
                    onClick = {
                        open = false
                        scope.launch {
                            withContext(Dispatchers.IO) { sessionsApi.setProvider(currentProvider, mid) }
                            onModelPicked(mid)
                            refreshKey++
                        }
                    },
                )
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Chat
// ---------------------------------------------------------------------------

@Composable
private fun ChatTab(
    sessionsApi: SessionsApi,
    api: RemedyApi,
    state: RemoteState,
    onStop: () -> Unit,
    preferredModel: String?,
    initialSessionId: String? = null,
    onInitialConsumed: () -> Unit = {},
) {
    var sessionId by remember { mutableStateOf<String?>(null) }
    var following by remember { mutableStateOf(true) }
    // A session picked from the Sessions tab opens here directly.
    LaunchedEffect(initialSessionId) {
        if (initialSessionId != null) {
            sessionId = initialSessionId
            following = false
            onInitialConsumed()
        }
    }
    // Follow the PC's active session: open it automatically and switch when
    // the desktop moves to another session. The phone mirrors what Remedy is
    // actually working on, instead of staying pinned to a stale session.
    LaunchedEffect(following) {
        if (!following) return@LaunchedEffect
        while (true) {
            val sid = withContext(Dispatchers.IO) { sessionsApi.activeSessionId() }
            if (sid != null) sessionId = sid
            delay(4000)
        }
    }
    if (sessionId == null) {
        SessionPicker(sessionsApi, preferredModel, onPick = { sessionId = it; following = false })
    } else {
        ChatScreen(
            sessionsApi, sessionId!!,
            onBack = { sessionId = null; following = false },
            onStop = onStop,
            following = following,
            onToggleFollow = { following = !following },
        )
    }
}

@Composable
private fun SessionPicker(sessionsApi: SessionsApi, preferredModel: String?, onPick: (String) -> Unit) {
    var sessions by remember { mutableStateOf<List<ChatSession>?>(null) }
    var creating by remember { mutableStateOf(false) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) {
        sessions = withContext(Dispatchers.IO) { sessionsApi.listSessions() }
    }
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Chat", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
            Spacer(Modifier.weight(1f))
            Button(
                onClick = {
                    creating = true
                    scope.launch {
                        val id = withContext(Dispatchers.IO) {
                            sessionsApi.createSession(model = preferredModel)
                        }
                        creating = false
                        if (id != null) onPick(id) else sessions = withContext(Dispatchers.IO) { sessionsApi.listSessions() }
                    }
                },
                colors = ButtonDefaults.buttonColors(containerColor = Accent),
                shape = RoundedCornerShape(12.dp),
                modifier = Modifier.height(40.dp),
            ) {
                Text(if (creating) "…" else "+ New", fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
            }
        }
        Spacer(Modifier.height(12.dp))
        if (preferredModel != null) {
            Text("New chats use $preferredModel", color = TextMuted, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
        }
        val list = sessions
        if (list == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = Accent, strokeWidth = 3.dp)
            }
        } else if (list.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("No sessions yet — start a new chat.", color = TextMuted)
            }
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(list, key = { it.id }) { s ->
                    SessionRow(s) { onPick(s.id) }
                }
            }
        }
    }
}

@Composable
private fun SessionRow(s: ChatSession, onClick: () -> Unit) {
    Card(
        Modifier.fillMaxWidth().clickable(onClick = onClick),
        shape = RoundedCornerShape(14.dp),
        colors = CardDefaults.cardColors(containerColor = BgSecondary),
        border = androidx.compose.foundation.BorderStroke(1.dp, Border),
    ) {
        Row(
            Modifier.padding(14.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            Column(Modifier.weight(1f)) {
                Text(s.displayTitle, color = TextPrimary, fontWeight = FontWeight.Medium, fontSize = 15.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
                Spacer(Modifier.height(3.dp))
                Row {
                    s.model?.let {
                        Text(it, color = TextMuted, fontSize = 12.sp)
                        Text(" · ", color = TextMuted, fontSize = 12.sp)
                    }
                    Text("${s.messageCount} msgs", color = TextMuted, fontSize = 12.sp)
                }
            }
            Icon(Icons.AutoMirrored.Filled.Send, contentDescription = null, tint = TextMuted, modifier = Modifier.size(18.dp))
        }
    }
}

@Composable
private fun ChatScreen(
    sessionsApi: SessionsApi,
    sessionId: String,
    onBack: () -> Unit,
    onStop: () -> Unit,
    following: Boolean,
    onToggleFollow: () -> Unit,
) {
    val messages = remember { mutableStateListOf<ChatMessage>() }
    val loading = remember { mutableStateOf(true) }
    var input by remember { mutableStateOf("") }
    var streaming by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    // State-backed so the DisposableEffect below sees the latest cancel
    // handle — a plain local is re-created on every recomposition.
    val streamCancel = remember { mutableStateOf<() -> Unit>({}) }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()

    LaunchedEffect(sessionId) {
        messages.clear()
        loading.value = true
        messages.addAll(withContext(Dispatchers.IO) { sessionsApi.messages(sessionId) })
        loading.value = false
    }
    // Loading a session with history: land at the bottom of the feed, and
    // keep following the newest message while a turn streams in.
    LaunchedEffect(messages.size, loading.value) {
        if (!loading.value && messages.isNotEmpty()) {
            listState.scrollToItem(messages.size - 1)
        }
    }
    DisposableEffect(sessionId) {
        onDispose { streamCancel.value() }
    }

    Column(Modifier.fillMaxSize()) {
        Row(
            Modifier.fillMaxWidth().padding(horizontal = 10.dp, vertical = 8.dp),
            verticalAlignment = Alignment.CenterVertically,
        ) {
            TextButton(onClick = onBack) { Text("←", color = TextSecondary, fontSize = 18.sp) }
            Text("Session", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 16.sp, modifier = Modifier.weight(1f), maxLines = 1, overflow = TextOverflow.Ellipsis)
            TextButton(onClick = onToggleFollow) {
                Text(if (following) "Following" else "Follow", color = if (following) Accent else TextMuted, fontSize = 12.sp)
            }
            if (streaming) {
                IconButton(onClick = onStop) {
                    Icon(Icons.Filled.Stop, contentDescription = "Stop", tint = Error, modifier = Modifier.size(22.dp))
                }
            }
        }
        Box(Modifier.weight(1f)) {
            if (loading.value) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = Accent, strokeWidth = 3.dp)
                }
            } else if (messages.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Text("Say hello 👋", color = TextMuted)
                }
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = androidx.compose.foundation.layout.PaddingValues(horizontal = 14.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(messages.size) { i ->
                        MessageBubble(messages[i])
                    }
                }
            }
        }
        if (error != null) {
            Text(error ?: "", color = Error, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 16.dp))
        }
        Row(
            Modifier.fillMaxWidth().imePadding().padding(horizontal = 12.dp, vertical = 10.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Message Remedy…", color = TextMuted) },
                maxLines = 4,
                shape = RoundedCornerShape(16.dp),
                colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Accent,
                    unfocusedBorderColor = Border,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary,
                    cursorColor = Accent,
                ),
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = {
                    val text = input.trim()
                    if (text.isEmpty() || streaming) return@Button
                    input = ""
                    streaming = true
                    error = null
                    messages.add(ChatMessage("local", "user", text, null, null, System.currentTimeMillis()))
                    val startIdx = messages.size
                    messages.add(ChatMessage("pending", "assistant", "", null, null, System.currentTimeMillis()))
                    scope.launch {
                        listState.scrollToItem((messages.size - 1).coerceAtLeast(0))
                    }
                    streamCancel.value = sessionsApi.sendStream(
                        sessionId,
                        text,
                        onToken = { tok ->
                            val i = messages.lastIndex
                            if (i >= startIdx) {
                                val cur = messages[i]
                                messages[i] = cur.copy(content = cur.content + tok)
                            }
                            scope.launch {
                                if (listState.layoutInfo.visibleItemsInfo.lastOrNull()?.index ?: 0 >= i - 2) {
                                    listState.scrollToItem(i)
                                }
                            }
                        },
                        onThinking = { /* fold into content for simplicity */ },
                        onTool = { tool -> onToolNote(messages, tool, startIdx) },
                        onDone = {
                            streaming = false
                            val i = messages.lastIndex
                            if (i >= startIdx && messages[i].content.isBlank()) {
                                messages.removeAt(i)
                            }
                            scope.launch { refreshTail(sessionsApi, sessionId, messages, startIdx) }
                        },
                        onError = { msg ->
                            streaming = false
                            error = msg
                        },
                    )
                },
                enabled = input.isNotBlank() && !streaming,
                colors = ButtonDefaults.buttonColors(containerColor = Accent),
                shape = CircleShape,
                modifier = Modifier.size(52.dp),
            ) {
                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send", tint = Color.White, modifier = Modifier.size(22.dp))
            }
        }
    }
}

private fun onToolNote(messages: androidx.compose.runtime.snapshots.SnapshotStateList<ChatMessage>, tool: String, fromIdx: Int) {
    val i = messages.lastIndex
    if (i >= fromIdx) {
        val cur = messages[i]
        messages[i] = cur.copy(content = cur.content + "\n· ${tool.replaceFirstChar { it.uppercase() }}")
    }
}

// Fetch on IO (the onDone callback lands on Main), mutate state on Main.
private suspend fun refreshTail(
    sessionsApi: SessionsApi,
    sessionId: String,
    messages: androidx.compose.runtime.snapshots.SnapshotStateList<ChatMessage>,
    fromIdx: Int,
) {
    val fresh = withContext(Dispatchers.IO) { sessionsApi.messages(sessionId, limit = 200) }
    if (fresh.isNotEmpty()) {
        while (messages.size > fromIdx) messages.removeAt(messages.lastIndex)
        messages.addAll(fresh)
    }
}

@Composable
private fun MessageBubble(m: ChatMessage) {
    val isUser = m.isUser
    val isPending = m.id == "pending"
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (isUser) Arrangement.End else Arrangement.Start) {
        Surface(
            shape = RoundedCornerShape(
                topStart = 16.dp, topEnd = 16.dp,
                bottomStart = if (isUser) 16.dp else 4.dp,
                bottomEnd = if (isUser) 4.dp else 16.dp,
            ),
            color = if (isUser) Accent.copy(alpha = 0.85f) else BgSecondary,
            border = androidx.compose.foundation.BorderStroke(1.dp, Border),
            modifier = Modifier.fillMaxWidth(if (isUser) 0.8f else 0.92f),
        ) {
            Column(Modifier.padding(12.dp)) {
                if (m.model != null && m.isAssistant && !isPending) {
                    Text(m.model, color = TextMuted, fontSize = 11.sp)
                    Spacer(Modifier.height(4.dp))
                }
                SelectionContainer {
                    Text(
                        m.content.ifBlank { "…" },
                        color = if (isUser) Color.White else TextPrimary,
                        fontSize = 15.sp,
                        lineHeight = 21.sp,
                    )
                }
                if (isPending) {
                    Spacer(Modifier.height(8.dp))
                    CircularProgressIndicator(color = Accent, strokeWidth = 2.dp, modifier = Modifier.size(16.dp))
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Sessions (history)
// ---------------------------------------------------------------------------

@Composable
private fun SessionsTab(
    sessionsApi: SessionsApi,
    onOpen: (ChatSession) -> Unit,
) {
    var sessions by remember { mutableStateOf<List<ChatSession>?>(null) }
    var refreshKey by remember { mutableIntStateOf(0) }
    LaunchedEffect(refreshKey) { sessions = withContext(Dispatchers.IO) { sessionsApi.listSessions() } }
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Session history", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
            Spacer(Modifier.weight(1f))
            IconButton(onClick = { refreshKey++ }) {
                Icon(Icons.Filled.Refresh, contentDescription = "Refresh", tint = TextSecondary, modifier = Modifier.size(20.dp))
            }
        }
        Spacer(Modifier.height(10.dp))
        val list = sessions
        if (list == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator(color = Accent, strokeWidth = 3.dp) }
        } else if (list.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Text("No sessions yet.", color = TextMuted) }
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(list, key = { it.id }) { s ->
                    Card(
                        Modifier.fillMaxWidth().clickable { onOpen(s) },
                        shape = RoundedCornerShape(14.dp),
                        colors = CardDefaults.cardColors(containerColor = BgSecondary),
                        border = androidx.compose.foundation.BorderStroke(1.dp, Border),
                    ) {
                        Column(Modifier.padding(14.dp)) {
                            Text(s.displayTitle, color = TextPrimary, fontWeight = FontWeight.Medium, fontSize = 15.sp, maxLines = 2, overflow = TextOverflow.Ellipsis)
                            Spacer(Modifier.height(4.dp))
                            Row {
                                s.model?.let {
                                    Text(it, color = TextMuted, fontSize = 12.sp)
                                    Text(" · ", color = TextMuted, fontSize = 12.sp)
                                }
                                Text("${s.messageCount} msgs", color = TextMuted, fontSize = 12.sp)
                                s.updatedAt?.let { ts ->
                                    Text(" · ", color = TextMuted, fontSize = 12.sp)
                                    Text(relTime(ts), color = TextMuted, fontSize = 12.sp)
                                }
                            }
                        }
                    }
                }
            }
        }
    }
}

private fun relTime(ms: Long): String {
    val d = System.currentTimeMillis() - ms
    return when {
        d < 60_000 -> "just now"
        d < 3_600_000 -> "${d / 60_000}m ago"
        d < 86_400_000 -> "${d / 3_600_000}h ago"
        else -> "${d / 86_400_000}d ago"
    }
}

// ---------------------------------------------------------------------------
// Approvals
// ---------------------------------------------------------------------------

@Composable
private fun ApprovalsTab(sessionsApi: SessionsApi) {
    var approvals by remember { mutableStateOf<List<Approval>?>(null) }
    var refreshKey by remember { mutableIntStateOf(0) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(refreshKey) { approvals = withContext(Dispatchers.IO) { sessionsApi.approvals() } }
    Column(Modifier.fillMaxSize().padding(16.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Approvals", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
            Spacer(Modifier.weight(1f))
            IconButton(onClick = { refreshKey++ }) {
                Icon(Icons.Filled.Refresh, contentDescription = "Refresh", tint = TextSecondary, modifier = Modifier.size(20.dp))
            }
        }
        Spacer(Modifier.height(10.dp))
        val list = approvals
        if (list == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator(color = Accent, strokeWidth = 3.dp) }
        } else if (list.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Column(horizontalAlignment = Alignment.CenterHorizontally) {
                    Icon(Icons.Filled.Check, contentDescription = null, tint = Success, modifier = Modifier.size(34.dp))
                    Spacer(Modifier.height(10.dp))
                    Text("Nothing needs you right now.", color = TextMuted)
                }
            }
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                items(list, key = { it.id }) { a ->
                    ApprovalCard(a, onResolve = { yes ->
                        scope.launch {
                            withContext(Dispatchers.IO) { sessionsApi.resolveApproval(a.id, yes) }
                            refreshKey++
                        }
                    })
                }
            }
        }
    }
}

@Composable
private fun ApprovalCard(a: Approval, onResolve: (Boolean) -> Unit) {
    Card(
        Modifier.fillMaxWidth(),
        shape = RoundedCornerShape(16.dp),
        colors = CardDefaults.cardColors(containerColor = BgSecondary),
        border = androidx.compose.foundation.BorderStroke(1.dp, Border),
    ) {
        Column(Modifier.padding(16.dp)) {
            Row(verticalAlignment = Alignment.CenterVertically) {
                Icon(Icons.Filled.Warning, contentDescription = null, tint = Warning, modifier = Modifier.size(18.dp))
                Spacer(Modifier.width(8.dp))
                Text("Remedy needs you", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
            }
            Spacer(Modifier.height(8.dp))
            Text(a.summary, color = TextPrimary, fontSize = 14.sp, lineHeight = 20.sp)
            if (a.sensitive) {
                Spacer(Modifier.height(4.dp))
                Text("Always asks — money, secrets, or send.", color = TextMuted, fontSize = 12.sp)
            }
            Spacer(Modifier.height(12.dp))
            Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Button(
                    onClick = { onResolve(true) },
                    colors = ButtonDefaults.buttonColors(containerColor = Accent),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.weight(1f).height(44.dp),
                ) {
                    Icon(Icons.Filled.Check, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Approve")
                }
                OutlinedButton(
                    onClick = { onResolve(false) },
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.weight(1f).height(44.dp),
                    colors = ButtonDefaults.outlinedButtonColors(contentColor = TextSecondary),
                    border = androidx.compose.foundation.BorderStroke(1.dp, Border),
                ) {
                    Icon(Icons.Filled.Clear, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(4.dp))
                    Text("Decline")
                }
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Terminal
// ---------------------------------------------------------------------------

@Composable
private fun TerminalTab(terminalApi: TerminalApi) {
    var tid by remember { mutableStateOf<String?>(null) }
    var input by remember { mutableStateOf("") }
    var running by remember { mutableStateOf(false) }
    var error by remember { mutableStateOf<String?>(null) }
    // State-backed so onDispose sees the live cancel handle, not a stale no-op.
    val cancelStream = remember { mutableStateOf<() -> Unit>({}) }
    val listState = rememberLazyListState()
    val lines = remember { mutableStateListOf<String>() }
    val scope = rememberCoroutineScope()

    DisposableEffect(Unit) {
        onDispose {
            cancelStream.value()
            // The composition scope is gone by now; fire-and-forget off Main.
            tid?.let { id -> CoroutineScope(Dispatchers.IO).launch { terminalApi.close(id) } }
        }
    }

    val ansiRe = Regex("\u001B\\[[0-9;?]*[ -/]*[@-~]|\u001B\\][^\u0007]*(?:\u0007|\u001B\\\\)")
    fun append(text: String) {
        val cleaned = ansiRe.replace(text, "").replace("\r\n", "\n").replace("\r", "\n")
        for (part in cleaned.split("\n")) {
            if (lines.isNotEmpty() && lines.last().endsWith("\u0000")) {
                lines[lines.lastIndex] = lines.last().dropLast(1) + part
            } else {
                lines.add(part)
            }
        }
        while (lines.size > 2000) lines.removeAt(0)
    }

    // Auto-start a shell when the tab opens — the Grove Connect terminal
    // should just work, no "open a shell" ceremony.
    LaunchedEffect(Unit) {
        if (tid == null) {
            val newTid = withContext(Dispatchers.IO) { terminalApi.open() }
            if (newTid != null) {
                tid = newTid
                running = true
                error = null
                cancelStream.value = terminalApi.stream(
                    newTid,
                    onOutput = { append(it) },
                    onExit = {
                        running = false
                        tid = null
                    },
                    onError = { msg ->
                        running = false
                        error = msg
                    },
                )
            } else {
                error = "Could not start a shell on the PC"
            }
        }
    }

    Column(Modifier.fillMaxSize().padding(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Terminal", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
            Spacer(Modifier.weight(1f))
            if (running) {
                Text("●", color = Success, fontSize = 14.sp)
                Spacer(Modifier.width(6.dp))
            }
            IconButton(onClick = {
                val closing = tid
                if (closing != null) {
                    cancelStream.value()
                    tid = null
                    lines.clear()
                    running = false
                }
                scope.launch {
                    if (closing != null) withContext(Dispatchers.IO) { terminalApi.close(closing) }
                    val newTid = withContext(Dispatchers.IO) { terminalApi.open() }
                    if (newTid != null) {
                        tid = newTid
                        running = true
                        error = null
                        cancelStream.value = terminalApi.stream(
                            newTid,
                            onOutput = { append(it) },
                            onExit = {
                                running = false
                                tid = null
                            },
                            onError = { msg ->
                                running = false
                                error = msg
                            },
                        )
                    } else {
                        error = "Could not start a shell"
                    }
                }
            }) {
                Icon(
                    if (tid != null) Icons.Filled.Clear else Icons.Filled.Terminal,
                    contentDescription = if (tid != null) "Close terminal" else "Open terminal",
                    tint = if (tid != null) Error else Accent,
                    modifier = Modifier.size(22.dp),
                )
            }
        }
        Spacer(Modifier.height(8.dp))
        if (error != null) {
            Text(error ?: "", color = Error, fontSize = 12.sp)
            Spacer(Modifier.height(8.dp))
        }
        Box(
            Modifier.weight(1f).fillMaxWidth().background(BgSecondary, RoundedCornerShape(12.dp))
                .border(1.dp, Border, RoundedCornerShape(12.dp))
                .padding(10.dp),
        ) {
            if (tid == null && lines.isEmpty()) {
                Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    Column(horizontalAlignment = Alignment.CenterHorizontally) {
                        Icon(Icons.Filled.Terminal, contentDescription = null, tint = TextMuted, modifier = Modifier.size(32.dp))
                        Spacer(Modifier.height(8.dp))
                        Text("Open a shell on your PC", color = TextMuted, fontSize = 13.sp)
                    }
                }
            } else {
                LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    verticalArrangement = Arrangement.spacedBy(2.dp),
                ) {
                    items(lines.size) { i ->
                        Text(
                            lines[i].ifBlank { " " },
                            color = TextPrimary,
                            fontFamily = FontFamily.Monospace,
                            fontSize = 13.sp,
                            lineHeight = 17.sp,
                        )
                    }
                }
            }
        }
        Spacer(Modifier.height(8.dp))
        Row(verticalAlignment = Alignment.Bottom) {
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text(if (tid != null) "Type a command…" else "Open a shell first", color = TextMuted) },
                enabled = tid != null,
                singleLine = true,
                shape = RoundedCornerShape(14.dp),
                colors = androidx.compose.material3.OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Accent,
                    unfocusedBorderColor = Border,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary,
                    cursorColor = Accent,
                ),
            )
            Spacer(Modifier.width(8.dp))
            Button(
                onClick = {
                    val text = input
                    if (text.isBlank()) return@Button
                    input = ""
                    val id = tid!!
                    scope.launch {
                        withContext(Dispatchers.IO) { terminalApi.input(id, text + "\n") }
                    }
                },
                enabled = tid != null && input.isNotBlank(),
                colors = ButtonDefaults.buttonColors(containerColor = Accent),
                shape = CircleShape,
                modifier = Modifier.size(50.dp),
            ) {
                Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send", tint = Color.White, modifier = Modifier.size(20.dp))
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Grove (goals + partner status, rendered natively)
// ---------------------------------------------------------------------------

@Composable
private fun GroveTab(api: RemedyApi) {
    // Grove is the goals/partner tab from Remedy Desktop — rendered natively,
    // never the live desktop UI on the phone.
    var goals by remember { mutableStateOf<List<GroveGoal>?>(null) }
    var partnerLine by remember { mutableStateOf<String?>(null) }
    var refreshKey by remember { mutableIntStateOf(0) }
    LaunchedEffect(refreshKey) {
        val (g, p) = withContext(Dispatchers.IO) {
            val gj = api.getJson("/api/goals")
            val list = gj?.optJSONArray("goals")?.let { arr ->
                buildList {
                    for (i in 0 until arr.length()) {
                        val o = arr.optJSONObject(i) ?: continue
                        add(
                            GroveGoal(
                                title = o.optString("title").ifBlank { o.optString("id") },
                                status = o.optString("status").ifBlank { "active" },
                            )
                        )
                    }
                }
            }
            val pj = api.getJson("/api/partner/status")
            val pl = pj?.optString("mood")?.takeIf { it.isNotBlank() }
                ?: pj?.optString("status")?.takeIf { it.isNotBlank() }
            list to pl
        }
        goals = g
        partnerLine = p
    }
    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Grove", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
            Spacer(Modifier.weight(1f))
            IconButton(onClick = { refreshKey++ }) {
                Icon(Icons.Filled.Refresh, contentDescription = "Refresh", tint = TextSecondary, modifier = Modifier.size(20.dp))
            }
        }
        partnerLine?.let { line ->
            Card(
                Modifier.fillMaxWidth(),
                shape = RoundedCornerShape(16.dp),
                colors = CardDefaults.cardColors(containerColor = BgSecondary),
                border = androidx.compose.foundation.BorderStroke(1.dp, Border),
            ) {
                Row(Modifier.padding(16.dp), verticalAlignment = Alignment.CenterVertically) {
                    Icon(Icons.Filled.Info, contentDescription = null, tint = Accent, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(10.dp))
                    Text(line, color = TextPrimary, fontWeight = FontWeight.Medium, fontSize = 14.sp)
                }
            }
        }
        val list = goals
        if (list == null) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                CircularProgressIndicator(color = Accent, strokeWidth = 3.dp)
            }
        } else if (list.isEmpty()) {
            Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                Text("No goals yet.", color = TextMuted)
            }
        } else {
            LazyColumn(verticalArrangement = Arrangement.spacedBy(8.dp)) {
                items(list.size) { i ->
                    val g = list[i]
                    Card(
                        Modifier.fillMaxWidth(),
                        shape = RoundedCornerShape(14.dp),
                        colors = CardDefaults.cardColors(containerColor = BgSecondary),
                        border = androidx.compose.foundation.BorderStroke(1.dp, Border),
                    ) {
                        Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically) {
                            Text(g.title, color = TextPrimary, fontWeight = FontWeight.Medium, fontSize = 14.sp, maxLines = 2, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                            Spacer(Modifier.width(10.dp))
                            Text(g.status, color = TextMuted, fontSize = 12.sp)
                        }
                    }
                }
            }
        }
    }
}

private data class GroveGoal(val title: String, val status: String)

// ---------------------------------------------------------------------------
// Settings
// ---------------------------------------------------------------------------

private fun pairsOf(arr: org.json.JSONArray?, preferName: Boolean): List<Pair<String, String>>? {
    if (arr == null) return null
    return buildList {
        for (i in 0 until arr.length()) {
            val o = arr.optJSONObject(i) ?: continue
            val id = o.optString("id").ifBlank { o.optString("name") }
            val name = o.optString("name").ifBlank { o.optString("id") }
            if (id.isNotBlank()) {
                val label = if (preferName) name.ifBlank { id } else id.ifBlank { name }
                add(id to label)
            }
        }
    }
}

private data class SettingsSnapshot(
    val ping: String?,
    val sessionId: String?,
    val deviceId: String?,
    val providers: List<Pair<String, String>>?,
    val models: List<Pair<String, String>>?,
    val currentProvider: String?,
    val currentModel: String?,
) {
    companion object {
        fun load(api: RemedyApi): SettingsSnapshot {
            val ping = api.getJson("/api/ping")?.optString("version")
            val conn = api.getJson("/connect/me")
            val sessionId = conn?.optString("session_id")?.takeIf { it.isNotBlank() }
            val deviceId = conn?.optString("device_id")?.takeIf { it.isNotBlank() }
            val settings = api.getJson("/api/settings")
            val currentProvider = settings?.optString("llm_provider")?.takeIf { it.isNotBlank() }
            val currentModel = settings?.optString("llm_model")?.takeIf { it.isNotBlank() }
            val provArr = api.getJson("/api/providers/connected")?.let { j ->
                j.optJSONArray("connected")
                    ?: j.optJSONArray("providers")
                    ?: j.optJSONArray("data")
            }
            val providers = pairsOf(provArr, preferName = true)
            val modelsArr = api.getJson("/api/models")?.let { j ->
                j.optJSONArray("models")
                    ?: j.optJSONArray("data")
                    ?: j.optJSONArray("items")
            }
            val models = pairsOf(modelsArr, preferName = false)
            return SettingsSnapshot(ping, sessionId, deviceId, providers, models, currentProvider, currentModel)
        }
    }
}

@Composable
private fun SettingsTab(
    api: RemedyApi,
    state: RemoteState,
    onStop: () -> Unit,
    onClose: () -> Unit,
    onModelPicked: (String?) -> Unit,
) {
    var me by remember { mutableStateOf<String?>(null) }
    var ping by remember { mutableStateOf<String?>(null) }
    var deviceId by remember { mutableStateOf<String?>(null) }
    var providers by remember { mutableStateOf<List<Pair<String, String>>?>(null) }
    var models by remember { mutableStateOf<List<Pair<String, String>>?>(null) }
    var chosenModel by remember { mutableStateOf<String?>(null) }
    var currentProvider by remember { mutableStateOf<String?>(null) }
    var revoking by remember { mutableStateOf(false) }
    var msg by remember { mutableStateOf<String?>(null) }
    var refreshKey by remember { mutableIntStateOf(0) }
    val sessionsApi = remember { SessionsApi(api) }
    val scope = rememberCoroutineScope()

    LaunchedEffect(refreshKey) {
        val snap = withContext(Dispatchers.IO) { SettingsSnapshot.load(api) }
        ping = snap.ping
        me = snap.sessionId
        deviceId = snap.deviceId
        providers = snap.providers
        models = snap.models
        currentProvider = snap.currentProvider
        chosenModel = snap.currentModel
    }

    Column(Modifier.fillMaxSize().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Row(verticalAlignment = Alignment.CenterVertically) {
            Text("Settings", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 18.sp)
            Spacer(Modifier.weight(1f))
            IconButton(onClick = { refreshKey++ }) {
                Icon(Icons.Filled.Refresh, contentDescription = "Refresh", tint = TextSecondary, modifier = Modifier.size(20.dp))
            }
        }
        if (msg != null) {
            Text(msg ?: "", color = Success, fontSize = 12.sp, modifier = Modifier.padding(horizontal = 4.dp))
        }

        Card(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = BgSecondary),
            border = androidx.compose.foundation.BorderStroke(1.dp, Border),
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Row(verticalAlignment = Alignment.CenterVertically) {
                    StatusDot(state.reachable)
                    Spacer(Modifier.width(10.dp))
                    Text(
                        when (state.reachable) {
                            Reachable.Connecting -> "Connecting…"
                            Reachable.OnLan -> "On your Wi-Fi"
                            Reachable.OnRelay -> "Via relay"
                            Reachable.Paused -> "Paused"
                        },
                        color = TextPrimary,
                        fontWeight = FontWeight.SemiBold,
                        fontSize = 16.sp,
                    )
                }
                state.lanLabel?.let {
                    Text(it, color = TextSecondary, fontSize = 14.sp)
                }
                ping?.let {
                    Text("Remedy $it", color = TextMuted, fontSize = 13.sp)
                }
                me?.let {
                    Text("session ${it.take(8)}…", color = TextMuted, fontSize = 13.sp)
                }
                Button(
                    onClick = onStop,
                    colors = ButtonDefaults.buttonColors(containerColor = Error),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth().height(44.dp),
                ) {
                    Icon(Icons.Filled.Stop, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text("Stop generation", fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                }
            }
        }

        Card(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = BgSecondary),
            border = androidx.compose.foundation.BorderStroke(1.dp, Border),
        ) {
            Column(Modifier.padding(16.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("Provider", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                val provs = providers
                if (provs == null) {
                    Text("Loading providers…", color = TextMuted, fontSize = 13.sp)
                } else if (provs.isEmpty()) {
                    Text("No providers connected on the PC.", color = TextMuted, fontSize = 13.sp)
                } else {
                    provs.forEach { (id, label) ->
                        val sel = id == currentProvider
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).clickable {
                                scope.launch {
                                    val ok = withContext(Dispatchers.IO) { sessionsApi.setProvider(id, null) }
                                    msg = if (ok) "Provider set to $label" else "Could not switch provider"
                                    refreshKey++
                                }
                            }.padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(label, color = if (sel) Accent else TextPrimary, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                            if (sel) Icon(Icons.Filled.Check, contentDescription = null, tint = Accent, modifier = Modifier.size(16.dp))
                        }
                    }
                }
                Spacer(Modifier.height(4.dp))
                Text("Model for new chats", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                val mods = models
                if (mods == null) {
                    Text("Loading models…", color = TextMuted, fontSize = 13.sp)
                } else {
                    Row(
                        Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).clickable {
                            scope.launch {
                                val ok = withContext(Dispatchers.IO) { sessionsApi.resetModel(currentProvider) }
                                chosenModel = null
                                onModelPicked(null)
                                msg = if (ok) "Model reset to default" else "Could not reset model"
                            }
                        }.padding(vertical = 8.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Text("Default", color = if (chosenModel == null) Accent else TextPrimary, fontSize = 13.sp, modifier = Modifier.weight(1f))
                        if (chosenModel == null) Icon(Icons.Filled.Check, contentDescription = null, tint = Accent, modifier = Modifier.size(16.dp))
                    }
                    mods.take(12).forEach { (mid, label) ->
                        val sel = chosenModel == mid
                        Row(
                            Modifier.fillMaxWidth().clip(RoundedCornerShape(10.dp)).clickable {
                                scope.launch {
                                    val ok = withContext(Dispatchers.IO) { sessionsApi.setProvider(currentProvider, mid) }
                                    chosenModel = mid
                                    onModelPicked(mid)
                                    msg = if (ok) "Model set to $label" else "Could not set model"
                                }
                            }.padding(vertical = 8.dp),
                            verticalAlignment = Alignment.CenterVertically,
                        ) {
                            Text(label, color = if (sel) Accent else TextPrimary, fontSize = 13.sp, maxLines = 1, overflow = TextOverflow.Ellipsis, modifier = Modifier.weight(1f))
                            if (sel) Icon(Icons.Filled.Check, contentDescription = null, tint = Accent, modifier = Modifier.size(16.dp))
                        }
                    }
                }
            }
        }

        Card(
            Modifier.fillMaxWidth(),
            shape = RoundedCornerShape(16.dp),
            colors = CardDefaults.cardColors(containerColor = BgSecondary),
            border = androidx.compose.foundation.BorderStroke(1.dp, Border),
        ) {
            Column(Modifier.padding(16.dp)) {
                Text("This phone", color = TextPrimary, fontWeight = FontWeight.SemiBold, fontSize = 15.sp)
                Spacer(Modifier.height(6.dp))
                Text(
                    "Revoking removes this phone from the PC. You will need to scan the QR again to reconnect.",
                    color = TextMuted,
                    fontSize = 12.sp,
                    lineHeight = 17.sp,
                )
                Spacer(Modifier.height(10.dp))
                Button(
                    onClick = {
                        val did = deviceId
                        if (did == null || revoking) {
                            msg = "No device id available — revoke from Remedy Desktop instead."
                            return@Button
                        }
                        revoking = true
                        msg = null
                        scope.launch {
                            val ok = withContext(Dispatchers.IO) {
                                api.postJson("/api/connect/devices/$did/revoke", null) != null
                            }
                            revoking = false
                            if (ok) onClose() else msg = "Revoke failed — the PC refused it."
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = Error),
                    shape = RoundedCornerShape(12.dp),
                    modifier = Modifier.fillMaxWidth().height(48.dp),
                ) {
                    Icon(Icons.Filled.Clear, contentDescription = null, modifier = Modifier.size(18.dp))
                    Spacer(Modifier.width(6.dp))
                    Text(if (revoking) "Revoking…" else "Revoke this phone", fontSize = 14.sp, fontWeight = FontWeight.SemiBold)
                }
                msg?.let {
                    Spacer(Modifier.height(8.dp))
                    Text(it, color = if (it.contains("failed")) Error else TextMuted, fontSize = 12.sp)
                }
            }
        }

        Text(
            "Your phone talks to your PC through the encrypted RemedyConnect tunnel. On Wi-Fi it is a direct LAN link; on mobile data it rides your Tailscale tailnet — no cloud in the middle.",
            color = TextMuted,
            fontSize = 12.sp,
            lineHeight = 17.sp,
        )
    }
}
