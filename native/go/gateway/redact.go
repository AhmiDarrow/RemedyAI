package gateway

import (
	"regexp"
	"strings"
)

var (
	tgBotURLRe       = regexp.MustCompile(`(?i)(https?://api\.telegram\.org/bot)([^/\s<>"']+)`)
	tgBotTokenRe     = regexp.MustCompile(`\b(\d{6,12}:[A-Za-z0-9_-]{20,})\b`)
	slackXoxRe       = regexp.MustCompile(`(?i)\bxox[baprs]-[A-Za-z0-9-]{10,}`)
	slackXappRe      = regexp.MustCompile(`(?i)\bxapp-[A-Za-z0-9-]{10,}`)
	discordWebhookRe = regexp.MustCompile(`(?i)(https?://(?:canary\.|ptb\.)?discord(?:app)?\.com/api/webhooks/\d+/)([A-Za-z0-9_\-]+)`)
	discordBotTokRe  = regexp.MustCompile(`\b([A-Za-z0-9_\-]{20,40}\.[A-Za-z0-9_\-]{4,10}\.[A-Za-z0-9_\-]{20,})\b`)
	matrixSytRe      = regexp.MustCompile(`\bsyt_[A-Za-z0-9._\-]{16,}\b`)
	bearerRe         = regexp.MustCompile(`(?i)\bbearer\s+[A-Za-z0-9._\-+/=]{8,}`)
)

// RedactSecrets scrubs messenger tokens/URLs from free text before logging.
func RedactSecrets(text string) string {
	if text == "" {
		return ""
	}
	out := tgBotURLRe.ReplaceAllString(text, `${1}[redacted]`)
	out = tgBotTokenRe.ReplaceAllString(out, "[redacted]")
	out = slackXoxRe.ReplaceAllString(out, "xox[redacted]")
	out = slackXappRe.ReplaceAllString(out, "xapp[redacted]")
	out = discordWebhookRe.ReplaceAllString(out, `${1}[redacted]`)
	out = discordBotTokRe.ReplaceAllString(out, "[redacted]")
	out = matrixSytRe.ReplaceAllString(out, "[redacted]")
	out = bearerRe.ReplaceAllString(out, "Bearer [redacted]")
	return out
}

// SafeErr truncates and redacts an error-ish value for logs.
func SafeErr(msg any) string {
	s := RedactSecrets(strings.TrimSpace(stringifyErr(msg)))
	if len(s) > 200 {
		return s[:200]
	}
	return s
}

func stringifyErr(msg any) string {
	if msg == nil {
		return ""
	}
	switch t := msg.(type) {
	case string:
		return t
	case error:
		return t.Error()
	default:
		return strings.TrimSpace(strings.ReplaceAll(
			strings.TrimSpace(defaultSprint(t)), "\n", " ",
		))
	}
}

func defaultSprint(v any) string {
	switch t := v.(type) {
	case string:
		return t
	case []byte:
		return string(t)
	default:
		return ""
	}
}
