package com.remedy.groveconnect.connect

import android.Manifest
import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.PackageManager
import android.os.Build
import androidx.core.content.ContextCompat
import com.remedy.groveconnect.MainActivity
import com.remedy.groveconnect.R

/**
 * "Remedy needs you" phone notifications. Posted only while the app is in the
 * background and only for questions the owner has not been told about yet.
 * The notification never answers anything — tapping it opens the app, which
 * asks for the screen lock first and then shows the one-at-a-time card.
 */
class NeedsYouNotifier(private val ctx: Context) {
    private val seen = HashSet<String>()

    /** Called with every approvals poll. */
    @Synchronized
    fun onDecisions(items: List<ApprovalItem>, foreground: Boolean) {
        val fresh = newDecisionIds(seen, items.map { it.id })
        seen.retainAll(items.map { it.id }.toSet())
        seen.addAll(items.map { it.id })
        if (items.isEmpty()) {
            cancel()
            return
        }
        if (foreground || fresh.isEmpty()) return
        val first = items.firstOrNull { it.id in fresh } ?: return
        post(first, items.size)
    }

    @Synchronized
    fun reset() {
        seen.clear()
        cancel()
    }

    private fun post(item: ApprovalItem, count: Int) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.TIRAMISU &&
            ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) !=
            PackageManager.PERMISSION_GRANTED
        ) {
            return
        }
        val nm = ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager
        if (nm.getNotificationChannel(CHANNEL) == null) {
            nm.createNotificationChannel(
                NotificationChannel(CHANNEL, ctx.getString(R.string.notif_needs_you_channel), NotificationManager.IMPORTANCE_HIGH),
            )
        }
        val open = PendingIntent.getActivity(
            ctx,
            2,
            Intent(ctx, MainActivity::class.java).addFlags(Intent.FLAG_ACTIVITY_SINGLE_TOP),
            PendingIntent.FLAG_UPDATE_CURRENT or PendingIntent.FLAG_IMMUTABLE,
        )
        val (title, text) = needsYouText(item.summary, item.sensitive, count)
        // The lock screen shows only "Remedy needs you" — the details wait
        // until the phone is unlocked.
        val public = Notification.Builder(ctx, CHANNEL)
            .setContentTitle(ctx.getString(R.string.notif_needs_you_title))
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .build()
        val n = Notification.Builder(ctx, CHANNEL)
            .setContentTitle(title)
            .setContentText(text)
            .setStyle(Notification.BigTextStyle().bigText(text))
            .setSmallIcon(android.R.drawable.ic_dialog_info)
            .setContentIntent(open)
            .setAutoCancel(true)
            .setCategory(Notification.CATEGORY_REMINDER)
            .setVisibility(Notification.VISIBILITY_PRIVATE)
            .setPublicVersion(public)
            .build()
        try {
            nm.notify(NOTIF_ID, n)
        } catch (_: SecurityException) {
        }
    }

    private fun cancel() {
        try {
            (ctx.getSystemService(Context.NOTIFICATION_SERVICE) as NotificationManager).cancel(NOTIF_ID)
        } catch (_: Exception) {
        }
    }

    private companion object {
        const val CHANNEL = "grove_connect_needs_you"
        const val NOTIF_ID = 7402
    }
}

/** Ids in [current] the owner has not been told about yet (pure; tested). */
fun newDecisionIds(seen: Set<String>, current: List<String>): Set<String> =
    current.filter { it.isNotBlank() && it !in seen }.toSet()

/** Title + body for a needs-you notification (pure; tested). */
fun needsYouText(summary: String, sensitive: Boolean, count: Int): Pair<String, String> {
    val title = when {
        sensitive -> "Remedy needs your OK — money, password or send"
        count > 1 -> "Remedy has $count questions for you"
        else -> "Remedy has a question for you"
    }
    val body = summary.trim().ifBlank { "Open RemedyConnect to answer." }
    return title to body
}
