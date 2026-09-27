package cognition

import (
	"encoding/json"
	"regexp"
	"strings"
)

// A missing module, command, or page is outside the tree. The note asks for
// one lookup. It does not stop the turn or cap a retry: the model decides
// what to do with the page it finds. Wording stays with the Python continue
// gate so the model hears one instruction.

var (
	knowledgeLine = regexp.MustCompile(`(?i)(` +
		`modulenotfounderror[^\n]{0,160}|` +
		`\bno module named\b[^\n]{0,160}|` +
		`importerror:[^\n]{0,160}|` +
		`\bcannot find module\b[^\n]{0,160}|` +
		`\bcommand not found\b[^\n]{0,120}|` +
		`is not recognized as an internal or external command[^\n]{0,80}|` +
		`could not find a version that satisfies[^\n]{0,160}|` +
		`no matching distribution found[^\n]{0,160}|` +
		`\bunknown command\b[^\n]{0,80}|` +
		`\bunrecognized (?:command|option|arguments)\b[^\n]{0,80}|` +
		`\bunknown (?:flag|option)\b[^\n]{0,80}|` +
		`\bhttp\s*404\b[^\n]{0,40}|` +
		`\b404 not found\b[^\n]{0,40}` +
		`)`)
)

type failNoteState struct {
	knowledgeNoted bool
}

func nextFailureNote(results []ToolResult, st *failNoteState) string {
	if st == nil || st.knowledgeNoted {
		return ""
	}
	for _, res := range results {
		text, failed := verifyFailed(res)
		if !failed || !knowledgeLine.MatchString(text) {
			continue
		}
		st.knowledgeNoted = true
		return knowledgeNote(res.Name)
	}
	return ""
}

func verifyFailed(res ToolResult) (string, bool) {
	if ClassifyTool(res.Name) != ToolVerify {
		return "", false
	}
	text := verifyFailureText(res)
	if res.Err != "" || res.IsError {
		return text, true
	}
	var body struct {
		ExitCode *int `json:"exit_code"`
	}
	if json.Unmarshal(res.Output, &body) == nil && body.ExitCode != nil && *body.ExitCode != 0 {
		return text, true
	}
	if knowledgeLine.MatchString(text) {
		return text, true
	}
	return "", false
}

func verifyFailureText(res ToolResult) string {
	parts := []string{res.Err}
	raw := string(res.Output)
	var body struct {
		Stdout string `json:"stdout"`
		Stderr string `json:"stderr"`
		Output string `json:"output"`
		Error  string `json:"error"`
		Detail string `json:"detail"`
	}
	if json.Unmarshal(res.Output, &body) == nil {
		parts = append(parts, body.Stderr, body.Stdout, body.Output, body.Error, body.Detail)
	}
	parts = append(parts, raw)
	text := strings.Join(parts, "\n")
	if len(text) > 4000 {
		text = text[len(text)-4000:]
	}
	return text
}

func knowledgeNote(name string) string {
	shown := strings.TrimSpace(name)
	if shown == "" {
		shown = "the check"
	}
	return "[Verify] `" + shown + "` failed on something outside this repo " +
		"(a missing module, an unknown command, or a missing page). " +
		"Look it up once with web.search before editing. " +
		"If that tool is off, say what you could not find."
}
