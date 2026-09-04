package httpapi

import (
	"regexp"
	"strings"
	"unicode"
)

var (
	drivePathRe     = regexp.MustCompile(`(?i)^[A-Za-z]:[\\/]`)
	pathExtRe       = regexp.MustCompile(`(?i)\.(png|jpe?g|gif|webp|bmp|heic|pdf|docx?)$`)
	screenshotExtRe = regexp.MustCompile(`(?i)\.(png|jpe?g|gif|webp)$`)
	imgExtRe        = regexp.MustCompile(`(?i)\.(png|jpe?g|gif|webp|bmp|heic)$`)
	screenshotPref  = regexp.MustCompile(`(?i)^Screenshot\b`)
	screenshotTail  = regexp.MustCompile(`\s+\d{4}.*$`)
)

var livingTitleSkip = map[string]struct{}{
	"ok": {}, "k": {}, "yes": {}, "y": {}, "no": {}, "n": {},
	"continue": {}, "thanks": {}, "thank you": {}, "hi": {}, "hey": {},
	"hello": {}, "yo": {}, "sup": {}, "stop": {}, "wait": {},
	"got it": {}, "cool": {}, "nice": {},
}

func looksLikePathTitle(text string) bool {
	t := strings.TrimSpace(text)
	if t == "" {
		return false
	}
	if drivePathRe.MatchString(t) {
		return true
	}
	if strings.HasPrefix(t, `\\`) || strings.HasPrefix(t, "/Users/") || strings.HasPrefix(t, "/home/") {
		return true
	}
	if strings.Contains(t, `\`) && pathExtRe.MatchString(t) {
		return true
	}
	return screenshotPref.MatchString(t) && screenshotExtRe.MatchString(t)
}

func titleFromAttachmentName(name string, maxLen int) string {
	if maxLen <= 0 {
		maxLen = 52
	}
	raw := strings.TrimSpace(strings.ReplaceAll(name, "/", `\`))
	if raw == "" {
		return "Attachment"
	}
	base := raw
	if i := strings.LastIndex(raw, `\`); i >= 0 {
		base = raw[i+1:]
	}
	pretty := imgExtRe.ReplaceAllString(base, "")
	pretty = strings.ReplaceAll(pretty, "_", " ")
	pretty = strings.ReplaceAll(pretty, "-", " ")
	pretty = strings.Join(strings.Fields(pretty), " ")
	if pretty == "" {
		pretty = "Image"
	}
	if screenshotPref.MatchString(pretty) {
		pretty = strings.TrimSpace(screenshotTail.ReplaceAllString(pretty, ""))
		if pretty == "" {
			pretty = "Screenshot"
		}
	}
	if len(pretty) > maxLen {
		pretty = strings.TrimRightFunc(pretty[:maxLen-1], unicode.IsSpace) + "…"
	}
	return pretty
}

func titleFromPrompt(text string, maxLen int, attDicts []map[string]any) string {
	if maxLen <= 0 {
		maxLen = 52
	}
	t := strings.Join(strings.Fields(strings.TrimSpace(text)), " ")
	if t == "" {
		return "New Session"
	}
	if strings.Contains(t, "📎") {
		if head, _, ok := strings.Cut(t, "📎"); ok {
			if h := strings.TrimSpace(head); h != "" {
				t = h
			}
		}
	}
	if strings.HasPrefix(t, "(") && strings.Contains(strings.ToLower(t), "see attached") {
		name := "Attachments"
		if len(attDicts) > 0 {
			if n, ok := attDicts[0]["name"].(string); ok && strings.TrimSpace(n) != "" {
				name = n
			}
		}
		t = titleFromAttachmentName(name, maxLen)
	} else if looksLikePathTitle(t) {
		t = titleFromAttachmentName(t, maxLen)
	}
	if len(t) > maxLen {
		t = strings.TrimRightFunc(t[:maxLen-1], unicode.IsSpace) + "…"
	}
	if t == "" {
		return "New Session"
	}
	return t
}

func isPlaceholderTitle(cur string) bool {
	t := strings.TrimSpace(cur)
	if t == "" {
		return true
	}
	switch strings.ToLower(t) {
	case "new session", "new chat", "untitled", "attachments", "attachment", "image", "screenshot":
		return true
	}
	return looksLikePathTitle(t)
}

func shouldRefreshLivingTitle(current, prompt string) bool {
	t := strings.Join(strings.Fields(strings.TrimSpace(prompt)), " ")
	if t == "" || looksLikePathTitle(t) {
		return false
	}
	low := strings.TrimRight(strings.ToLower(t), "!.?")
	if _, skip := livingTitleSkip[low]; skip {
		return false
	}
	if len(t) < 16 && !strings.Contains(t, "?") {
		return false
	}
	cur := strings.TrimSpace(current)
	if cur == "" || isPlaceholderTitle(cur) {
		return true
	}
	newTitle := titleFromPrompt(t, 52, nil)
	return newTitle != "" && !strings.EqualFold(newTitle, cur)
}

func maybeAutoTitle(sess ChatSession, userText string, attDicts []map[string]any) (string, bool) {
	cur := strings.TrimSpace(sess.Title)
	placeholder := isPlaceholderTitle(cur)
	if placeholder && (strings.TrimSpace(userText) != "" || len(attDicts) > 0) {
		seed := userText
		if strings.TrimSpace(seed) == "" {
			seed = "Attachments"
			if len(attDicts) > 0 {
				if n, ok := attDicts[0]["name"].(string); ok && strings.TrimSpace(n) != "" {
					seed = n
				}
			}
		}
		newTitle := titleFromPrompt(seed, 52, attDicts)
		if strings.TrimSpace(userText) != "" && !looksLikePathTitle(newTitle) {
			return newTitle, true
		}
		if looksLikePathTitle(cur) || cur == "" {
			return newTitle, true
		}
	}
	if strings.TrimSpace(userText) != "" && shouldRefreshLivingTitle(cur, userText) {
		newTitle := titleFromPrompt(userText, 52, attDicts)
		if newTitle != "" && !looksLikePathTitle(newTitle) {
			return newTitle, true
		}
	}
	return "", false
}
