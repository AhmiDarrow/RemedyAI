package com.remedy.groveconnect.ui

import android.app.Activity
import android.content.ActivityNotFoundException
import android.content.Intent
import android.speech.RecognizerIntent
import android.widget.Toast
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.BorderStroke
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
import androidx.compose.foundation.layout.width
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.filled.Send
import androidx.compose.material.icons.filled.Mic
import androidx.compose.material.icons.filled.Stop
import androidx.compose.material3.Button
import androidx.compose.material3.ButtonDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.OutlinedTextFieldDefaults
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.DisposableEffect
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateListOf
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.rememberCoroutineScope
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.semantics.LiveRegionMode
import androidx.compose.ui.semantics.liveRegion
import androidx.compose.ui.semantics.semantics
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import com.remedy.groveconnect.api.ChatMessage
import com.remedy.groveconnect.api.HeadOverview
import com.remedy.groveconnect.api.MAX_RESUME_ATTEMPTS
import com.remedy.groveconnect.api.PartnerApi
import com.remedy.groveconnect.api.ResumeAction
import com.remedy.groveconnect.api.SessionsApi
import com.remedy.groveconnect.api.TurnMark
import com.remedy.groveconnect.api.headSummary
import com.remedy.groveconnect.api.resumeActionFor
import com.remedy.groveconnect.api.resumeDelayMs
import com.remedy.groveconnect.ui.theme.Accent
import com.remedy.groveconnect.ui.theme.BgSecondary
import com.remedy.groveconnect.ui.theme.Border
import com.remedy.groveconnect.ui.theme.Error
import com.remedy.groveconnect.ui.theme.TextMuted
import com.remedy.groveconnect.ui.theme.TextPrimary
import com.remedy.groveconnect.ui.theme.TextSecondary
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

private const val PENDING = "pending"

private fun pendingBubble() = ChatMessage(PENDING, "assistant", "", null, null, System.currentTimeMillis())

/**
 * Talk: Remedy's own conversation — the same one Grove shows on the PC.
 * Replies stream in; if the link drops mid-reply the phone re-attaches after
 * the last frame it saw, and a reply already running on the PC shows live.
 */
@Composable
internal fun TalkTab(
    sessions: SessionsApi,
    partner: PartnerApi,
    head: HeadOverview?,
    questions: Int,
    connected: Boolean,
) {
    var sessionId by remember { mutableStateOf<String?>(null) }
    var loadError by remember { mutableStateOf<String?>(null) }
    val messages = remember { mutableStateListOf<ChatMessage>() }
    var loaded by remember { mutableStateOf(false) }
    var input by remember { mutableStateOf("") }
    var live by remember { mutableStateOf(false) }
    var note by remember { mutableStateOf<String?>(null) }
    val mark = remember { mutableStateOf(TurnMark()) }
    val cancel = remember { mutableStateOf<() -> Unit>({}) }
    val listState = rememberLazyListState()
    val scope = rememberCoroutineScope()
    val ctx = LocalContext.current

    suspend fun reload(sid: String) {
        val fresh = withContext(Dispatchers.IO) { sessions.messages(sid, limit = 200) }
        messages.clear()
        messages.addAll(fresh.filter { it.content.isNotBlank() && (it.isUser || it.isAssistant) })
        loaded = true
    }

    fun onToken(tok: String) {
        val i = messages.lastIndex
        if (i < 0 || messages[i].id != PENDING) messages.add(pendingBubble())
        val j = messages.lastIndex
        messages[j] = messages[j].copy(content = messages[j].content + tok)
    }

    fun onTool(tool: String) {
        note = "Working: " + tool.replace('_', ' ')
    }

    // Remedy's head conversation.
    LaunchedEffect(connected, head?.headSessionId) {
        if (!connected) return@LaunchedEffect
        val known = head?.headSessionId
        if (sessionId != null && (known == null || known == sessionId)) return@LaunchedEffect
        val sid = known ?: withContext(Dispatchers.IO) { partner.headSessionId() }
        if (sid == null) {
            loadError = "Couldn't open Remedy's conversation on your PC."
            return@LaunchedEffect
        }
        loadError = null
        sessionId = sid
        reload(sid)
    }
    LaunchedEffect(messages.size, loaded) {
        if (loaded && messages.isNotEmpty()) listState.scrollToItem(messages.size - 1)
    }
    DisposableEffect(Unit) { onDispose { cancel.value() } }

    fun finished(sid: String) {
        live = false
        note = null
        mark.value = TurnMark()
        scope.launch { reload(sid) }
    }

    // Re-attach after a drop, with backoff. HTTP 4xx = the turn is over.
    fun resume(sid: String, error: String, attempt: Int) {
        when (resumeActionFor(error)) {
            ResumeAction.Reload -> finished(sid)
            ResumeAction.Retry -> {
                if (attempt >= MAX_RESUME_ATTEMPTS) {
                    live = false
                    note = "Lost the reply — it will show here once your PC is back."
                    return
                }
                note = "Connection dropped — picking the reply back up…"
                scope.launch {
                    delay(resumeDelayMs(attempt))
                    val m = mark.value
                    cancel.value = sessions.attachStream(
                        sid, m.requestId, m.seq,
                        onToken = ::onToken,
                        onThinking = {},
                        onTool = ::onTool,
                        onDone = { finished(sid) },
                        onError = { resume(sid, it, attempt + 1) },
                        onMark = {
                            mark.value = it
                            note = null
                        },
                    )
                }
            }
        }
    }

    // On open: if Remedy is already mid-reply (started on the PC), show it live.
    LaunchedEffect(sessionId, loaded) {
        val sid = sessionId ?: return@LaunchedEffect
        if (!loaded || live) return@LaunchedEffect
        cancel.value = sessions.attachStream(
            sid, null, 0,
            onToken = {
                live = true
                onToken(it)
            },
            onThinking = {},
            onTool = {
                live = true
                onTool(it)
            },
            onDone = { if (live) finished(sid) },
            onError = { /* nothing running — the normal case */ },
            onMark = { mark.value = it },
        )
    }

    val voice = rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { res ->
        if (res.resultCode == Activity.RESULT_OK) {
            val said = res.data?.getStringArrayListExtra(RecognizerIntent.EXTRA_RESULTS)?.firstOrNull()
            if (!said.isNullOrBlank()) input = appendSpoken(input, said)
        }
    }

    fun send() {
        val sid = sessionId ?: return
        val text = input.trim()
        if (text.isEmpty() || live) return
        input = ""
        note = null
        messages.add(ChatMessage("local", "user", text, null, null, System.currentTimeMillis()))
        messages.add(pendingBubble())
        mark.value = TurnMark()
        live = true
        cancel.value = sessions.sendStream(
            sid, text,
            onToken = ::onToken,
            onThinking = {},
            onTool = ::onTool,
            onDone = { finished(sid) },
            onError = { resume(sid, it, 0) },
            onMark = { mark.value = it },
        )
    }

    Column(Modifier.fillMaxSize()) {
        Text(
            headSummary(head, questions),
            color = TextSecondary,
            fontSize = 14.sp,
            modifier = Modifier.fillMaxWidth().padding(horizontal = 16.dp, vertical = 4.dp)
                .semantics { liveRegion = LiveRegionMode.Polite },
        )
        Box(Modifier.weight(1f)) {
            when {
                loadError != null -> Blocked(loadError ?: "")
                !loaded -> Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) {
                    CircularProgressIndicator(color = Accent, strokeWidth = 3.dp)
                }
                messages.isEmpty() -> Blocked(
                    "Say hi, or tell Remedy what you'd like done. She works on your PC and keeps you posted here.",
                )
                else -> LazyColumn(
                    state = listState,
                    modifier = Modifier.fillMaxSize(),
                    contentPadding = PaddingValues(horizontal = 12.dp, vertical = 8.dp),
                    verticalArrangement = Arrangement.spacedBy(10.dp),
                ) {
                    items(messages.size) { i -> TalkBubble(messages[i]) }
                }
            }
        }
        note?.let {
            Text(
                it,
                color = TextMuted,
                fontSize = 13.sp,
                maxLines = 2,
                overflow = TextOverflow.Ellipsis,
                modifier = Modifier.padding(horizontal = 16.dp).semantics { liveRegion = LiveRegionMode.Polite },
            )
        }
        Row(
            Modifier.fillMaxWidth().imePadding().padding(horizontal = 8.dp, vertical = 8.dp),
            verticalAlignment = Alignment.Bottom,
        ) {
            IconButton(
                onClick = {
                    val i = Intent(RecognizerIntent.ACTION_RECOGNIZE_SPEECH)
                        .putExtra(RecognizerIntent.EXTRA_LANGUAGE_MODEL, RecognizerIntent.LANGUAGE_MODEL_FREE_FORM)
                        .putExtra(RecognizerIntent.EXTRA_PROMPT, "Talk to Remedy")
                    try {
                        voice.launch(i)
                    } catch (_: ActivityNotFoundException) {
                        Toast.makeText(ctx, "Voice input isn't available on this phone.", Toast.LENGTH_LONG).show()
                    }
                },
                enabled = connected,
                modifier = Modifier.size(52.dp),
            ) {
                Icon(
                    Icons.Filled.Mic,
                    contentDescription = "Speak a message",
                    tint = if (connected) Accent else TextMuted,
                    modifier = Modifier.size(28.dp),
                )
            }
            OutlinedTextField(
                value = input,
                onValueChange = { input = it },
                modifier = Modifier.weight(1f),
                placeholder = { Text("Talk to Remedy…", color = TextMuted) },
                maxLines = 5,
                textStyle = TextStyle(fontSize = 16.sp),
                shape = RoundedCornerShape(18.dp),
                colors = OutlinedTextFieldDefaults.colors(
                    focusedBorderColor = Accent,
                    unfocusedBorderColor = Border,
                    focusedTextColor = TextPrimary,
                    unfocusedTextColor = TextPrimary,
                    cursorColor = Accent,
                ),
            )
            Spacer(Modifier.width(8.dp))
            if (live) {
                Button(
                    onClick = {
                        val sid = sessionId ?: return@Button
                        scope.launch {
                            val ok = withContext(Dispatchers.IO) { sessions.stop(sid) }
                            if (!ok) note = "Couldn't stop — try again."
                        }
                    },
                    colors = ButtonDefaults.buttonColors(containerColor = Error),
                    shape = CircleShape,
                    contentPadding = PaddingValues(0.dp),
                    modifier = Modifier.size(52.dp),
                ) { Icon(Icons.Filled.Stop, contentDescription = "Stop Remedy's reply", tint = Color.White) }
            } else {
                Button(
                    onClick = { send() },
                    enabled = connected && sessionId != null && input.isNotBlank(),
                    colors = ButtonDefaults.buttonColors(containerColor = Accent),
                    shape = CircleShape,
                    contentPadding = PaddingValues(0.dp),
                    modifier = Modifier.size(52.dp),
                ) { Icon(Icons.AutoMirrored.Filled.Send, contentDescription = "Send", tint = Color.White) }
            }
        }
    }
}

/** Add dictated words to what is already typed (pure; tested). */
internal fun appendSpoken(current: String, said: String): String =
    if (current.isBlank()) said.trim() else current.trimEnd() + " " + said.trim()

@Composable
private fun TalkBubble(m: ChatMessage) {
    val mine = m.isUser
    val pending = m.id == PENDING
    Row(Modifier.fillMaxWidth(), horizontalArrangement = if (mine) Arrangement.End else Arrangement.Start) {
        Surface(
            shape = RoundedCornerShape(
                topStart = 18.dp,
                topEnd = 18.dp,
                bottomStart = if (mine) 18.dp else 4.dp,
                bottomEnd = if (mine) 4.dp else 18.dp,
            ),
            color = if (mine) Accent.copy(alpha = 0.9f) else BgSecondary,
            border = if (mine) null else BorderStroke(1.dp, Border),
            modifier = Modifier.fillMaxWidth(if (mine) 0.82f else 0.94f),
        ) {
            Column(Modifier.padding(horizontal = 14.dp, vertical = 10.dp)) {
                SelectionContainer {
                    Text(
                        m.content.ifBlank { if (pending) "Thinking…" else "" },
                        color = if (mine) Color.White else TextPrimary,
                        fontSize = 16.sp,
                        lineHeight = 23.sp,
                    )
                }
                if (pending) {
                    Spacer(Modifier.height(6.dp))
                    CircularProgressIndicator(color = Accent, strokeWidth = 2.dp, modifier = Modifier.size(16.dp))
                }
            }
        }
    }
}
