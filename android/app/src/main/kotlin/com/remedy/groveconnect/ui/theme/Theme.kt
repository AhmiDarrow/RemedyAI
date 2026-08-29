package com.remedy.groveconnect.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

/** Dark Forest tokens from desktop/src/index.css. */
val BgPrimary = Color(0xFF0A0E0B)
val BgSecondary = Color(0xFF121812)
val BgTertiary = Color(0xFF1A221C)
val Border = Color(0xFF2A352C)
val Accent = Color(0xFF4D7A5A)
val AccentHover = Color(0xFF3D6349)
val TextPrimary = Color(0xFFE6EBE7)
val TextSecondary = Color(0xFF9AA89E)
val TextMuted = Color(0xFF6B7870)
val Success = Color(0xFF5A8F6A)
val Error = Color(0xFFB87A7A)
val Warning = Color(0xFFA89058)

private val GroveColors = darkColorScheme(
    primary = Accent,
    onPrimary = Color.White,
    secondary = BgTertiary,
    onSecondary = TextPrimary,
    background = BgPrimary,
    onBackground = TextPrimary,
    surface = BgSecondary,
    onSurface = TextPrimary,
    surfaceVariant = BgTertiary,
    onSurfaceVariant = TextSecondary,
    outline = Border,
    error = Error,
    onError = Color.White,
)

@Composable
fun GroveTheme(content: @Composable () -> Unit) {
    MaterialTheme(
        colorScheme = GroveColors,
        content = content,
    )
}
