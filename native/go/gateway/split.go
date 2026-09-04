package gateway

import "strings"

// MaxReplyChars returns the soft platform reply cap.
func MaxReplyChars(channel ChannelKind) int {
	switch ChannelKind(normalizeID(string(channel))) {
	case ChannelTelegram, ChannelWhatsApp, ChannelGoogleChat:
		return 4096
	case ChannelDiscord:
		return 2000
	case ChannelSlack:
		return 3000
	case ChannelSignal:
		return 2000
	case ChannelTeams:
		return 28000
	default:
		return 4000
	}
}

// SplitMessage breaks a long reply to fit platform limits.
func SplitMessage(text string, channel ChannelKind) []string {
	limit := MaxReplyChars(channel)
	text = text
	if text == "" {
		return nil
	}
	if len(text) <= limit {
		return []string{text}
	}
	parts := make([]string, 0, len(text)/limit+1)
	rest := text
	for rest != "" {
		if len(rest) <= limit {
			parts = append(parts, rest)
			break
		}
		cut := strings.LastIndex(rest[:limit], "\n")
		if cut < limit/3 {
			cut = strings.LastIndex(rest[:limit], " ")
		}
		if cut < limit/3 {
			cut = limit
		}
		parts = append(parts, strings.TrimRight(rest[:cut], " \t"))
		rest = strings.TrimLeft(rest[cut:], " \t")
	}
	return parts
}

// ExternalSessionID builds a stable chat_session id for a messenger conversation.
func ExternalSessionID(channel, externalChatID, threadID string) string {
	ch := normalizeID(channel)
	if ch == "" {
		ch = "unknown"
	}
	ext := strings.TrimSpace(externalChatID)
	if ext == "" {
		ext = "default"
	}
	ext = sanitizeID(ext, 180)
	tid := strings.TrimSpace(threadID)
	if tid != "" {
		return "msg:" + ch + ":" + ext + ":" + sanitizeID(tid, 80)
	}
	return "msg:" + ch + ":" + ext
}

func sanitizeID(s string, max int) string {
	var b strings.Builder
	for _, r := range s {
		switch {
		case r >= 'a' && r <= 'z', r >= 'A' && r <= 'Z', r >= '0' && r <= '9':
			b.WriteRune(r)
		case r == '_' || r == '.' || r == '@' || r == '+' || r == '-':
			b.WriteRune(r)
		default:
			b.WriteByte('_')
		}
		if b.Len() >= max {
			break
		}
	}
	return b.String()
}

// HeuristicSessionTitle builds a fast title without calling a model.
func HeuristicSessionTitle(channel ChannelKind, username, chatTitle, firstMessage string) string {
	label := messengerBadge(channel)
	if strings.TrimSpace(chatTitle) != "" {
		t := strings.TrimSpace(chatTitle)
		if len(t) > 60 {
			t = t[:60]
		}
		return label + " · " + t
	}
	if strings.TrimSpace(username) != "" {
		u := strings.TrimSpace(username)
		if channel == ChannelTelegram && !strings.HasPrefix(u, "@") {
			u = "@" + u
		}
		if len(u) > 60 {
			u = u[:60]
		}
		return label + " · " + u
	}
	if strings.TrimSpace(firstMessage) != "" {
		snippet := strings.Join(strings.Fields(firstMessage), " ")
		if len(snippet) > 48 {
			snippet = snippet[:48] + "…"
		}
		return label + " · " + snippet
	}
	return label + " chat"
}

func messengerBadge(ch ChannelKind) string {
	switch ChannelKind(normalizeID(string(ch))) {
	case ChannelTelegram:
		return "Telegram"
	case ChannelDiscord:
		return "Discord"
	case ChannelSlack:
		return "Slack"
	case ChannelMattermost:
		return "Mattermost"
	case ChannelWhatsApp:
		return "WhatsApp"
	case ChannelTeams:
		return "Teams"
	case ChannelMatrix:
		return "Matrix"
	case ChannelGoogleChat:
		return "GChat"
	case ChannelSignal:
		return "Signal"
	default:
		s := string(ch)
		if s == "" {
			return "Messenger"
		}
		return strings.ToUpper(s[:1]) + s[1:]
	}
}
