package prompt

import (
	"encoding/json"
	"fmt"
	"strings"
	"unicode"
)

// Turn classification from react_policy. Patterns are generated from the
// Python (pytext_gen.go). Go's \b, \w and \s are ASCII where Python's are
// Unicode, so the two can differ only where a keyword touches a non-ASCII
// letter or space.

// coerceText is build_oracle.coerce_text_arg for a string value.
func coerceText(raw string) string {
	text := pyStrip(raw)
	if len(text) >= 2 && text[0] == '[' && text[len(text)-1] == ']' {
		var parsed []any
		if json.Unmarshal([]byte(text), &parsed) == nil {
			return coerceAny(parsed, " ")
		}
	}
	return text
}

func coerceAny(v any, sep string) string {
	switch t := v.(type) {
	case nil:
		return ""
	case bool:
		if t {
			return "true"
		}
		return "false"
	case string:
		return coerceText(t)
	case []any:
		var parts []string
		for _, x := range t {
			if p := coerceAny(x, sep); p != "" {
				parts = append(parts, p)
			}
		}
		return pyStrip(strings.Join(parts, sep))
	case map[string]any:
		for _, k := range []string{"text", "content", "title", "goal", "value"} {
			if inner, ok := t[k]; ok {
				if s := coerceAny(inner, sep); s != "" {
					return s
				}
			}
		}
		b, err := json.Marshal(t)
		if err != nil {
			return ""
		}
		return string(b)
	case float64:
		return pyStrip(fmt.Sprint(t))
	}
	return pyStrip(fmt.Sprint(v))
}

// isVocativeName is react_policy._is_vocative_name.
func isVocativeName(rest string) bool {
	tok := strings.TrimRight(pyStrip(rest), "!.?")
	if tok == "" || strings.Contains(tok, " ") || cpLen(tok) > 24 {
		return false
	}
	if actionKickRE.MatchString(tok) {
		return false
	}
	cleaned := strings.NewReplacer("-", "", "'", "").Replace(tok)
	if cleaned == "" {
		return false
	}
	for _, r := range cleaned {
		if !unicode.IsLetter(r) {
			return false
		}
	}
	return true
}

func inChatShortSet(s string) bool {
	_, ok := chatShortSet[s]
	return ok
}

// isChatOnlyMessage is react_policy.is_chat_only_message.
func isChatOnlyMessage(message string) bool {
	msg := coerceText(message)
	if msg == "" {
		return true
	}
	if actionKickRE.MatchString(msg) {
		return false
	}
	rest := msg
	if loc := greetPrefixRE.FindStringIndex(msg); loc != nil {
		rest = msg[loc[1]:]
	}
	rest = pyStrip(rest)
	if rest != "" && rest != msg {
		restLow := strings.TrimRight(strings.ToLower(rest), "!.?")
		return inChatShortSet(restLow) ||
			inChatShortSet(strings.ToLower(rest)) ||
			restLow == "there" || restLow == "again" || restLow == "friend" || restLow == "buddy" ||
			hardChatOnlyRE.MatchString(rest) || chatOnlyRE.MatchString(rest) ||
			isVocativeName(rest)
	}
	if cpLen(msg) <= 24 && !strings.Contains(msg, "\n") {
		low := strings.TrimRight(strings.ToLower(msg), "!.?")
		if inChatShortSet(low) || inChatShortSet(strings.ToLower(msg)) {
			return true
		}
	}
	if hardChatOnlyRE.MatchString(msg) || chatOnlyRE.MatchString(msg) {
		return true
	}
	if cpLen(msg) <= 80 && socialReassureRE.MatchString(msg) {
		return true
	}
	return cpLen(msg) <= 80 && metaNoToolsRE.MatchString(msg)
}

// turnIsChatOnly is react_policy.runtime_turn_is_chat_only for this turn's
// user text: empty text is not chat-only.
func turnIsChatOnly(message string) bool {
	msg := coerceText(message)
	if msg == "" {
		return false
	}
	return isChatOnlyMessage(msg)
}

// isFeelingPresenceQuestion is react_policy.is_feeling_presence_question.
func isFeelingPresenceQuestion(message string) bool {
	msg := coerceText(message)
	if msg == "" || strings.Contains(msg, "\n") || cpLen(msg) > 120 {
		return false
	}
	if actionKickRE.MatchString(msg) || requestWorkRE.MatchString(msg) {
		return false
	}
	if hostWorkRE.MatchString(msg) || debugFollowupRE.MatchString(msg) {
		return false
	}
	return feelingPresenceRE.MatchString(msg)
}
