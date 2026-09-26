package cognition

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"strings"
)

const (
	// compactKeepTail is how many trailing messages survive compaction verbatim.
	compactKeepTail = 8
	// compactedHeader opens the deterministic replacement message.
	compactedHeader = "[Compacted context]"
)

// compactTranscript replaces the middle of an over-budget transcript with one
// deterministic working-set message. The first user prose (session opener),
// the latest user prose that is not tool results (this turn's goal), and the
// last compactKeepTail messages survive verbatim. The cut never separates an
// assistant tool_use message from the user message carrying its results.
func compactTranscript(msgs []Message) []Message {
	if len(msgs) <= compactKeepTail+2 {
		return msgs
	}
	head := firstUserProseIndex(msgs)
	goal := lastUserProseIndex(msgs)
	cut := len(msgs) - compactKeepTail
	// Never start the kept tail on tool results whose tool_use is being dropped.
	for cut > 1 && msgs[cut].Role == RoleUser && msgs[cut].HasToolResults() {
		cut--
	}
	if cut <= head+1 && (goal < 0 || goal == head || goal >= cut) {
		return msgs
	}
	keepGoal := goal > head && goal < cut
	middle := make([]Message, 0, cut-head)
	for i := head + 1; i < cut; i++ {
		if keepGoal && i == goal {
			continue
		}
		middle = append(middle, msgs[i])
	}
	if len(middle) == 0 {
		return msgs
	}
	out := make([]Message, 0, 4+len(msgs)-cut)
	out = append(out, msgs[head])
	if keepGoal {
		out = append(out, msgs[goal])
	}
	out = append(out, contextNote(buildWorkingSet(middle).render(len(middle))))
	out = append(out, msgs[cut:]...)
	return out
}

func firstUserProseIndex(msgs []Message) int {
	for i, m := range msgs {
		if m.Role == RoleUser && !m.Internal && !m.HasToolResults() && strings.TrimSpace(m.Text()) != "" {
			return i
		}
	}
	return 0
}

func lastUserProseIndex(msgs []Message) int {
	for i := len(msgs) - 1; i >= 0; i-- {
		if msgs[i].Role == RoleUser && !msgs[i].Internal && !msgs[i].HasToolResults() && strings.TrimSpace(msgs[i].Text()) != "" {
			return i
		}
	}
	return -1
}

type fileNote struct {
	path string
	hash string
}

type commandNote struct {
	command string
	exit    string
}

// workingSet is the deterministic residue of the rounds a compaction drops.
type workingSet struct {
	carried   string
	read      []fileNote
	edited    []string
	commands  []commandNote
	failure   string
	todo      string
	decisions []string
	requests  []string
}

const (
	maxWorkingSetFiles     = 40
	maxWorkingSetCommands  = 30
	maxWorkingSetDecisions = 16
	maxFailureChars        = 1_200
	maxTodoChars           = 1_200
	maxCarriedChars        = 4_000
	maxWorkingSetRequests  = 16
	maxRequestChars        = 1_200
)

// buildWorkingSet walks the dropped middle of a transcript and records what the
// model must not forget: files it touched, commands it ran with exit codes, the
// last failing check, the TODO state and one decision line per assistant round.
func buildWorkingSet(msgs []Message) workingSet {
	var ws workingSet
	calls := map[string]Block{}
	for _, m := range msgs {
		for _, b := range m.Blocks {
			switch b.Type {
			case BlockText:
				if m.Role == RoleAssistant {
					if line := firstSentence(b.Text); line != "" {
						ws.decisions = appendRecent(ws.decisions, line, maxWorkingSetDecisions)
					}
					continue
				}
				// A second compaction must not throw the first one away: the
				// previous working set is carried forward, bounded.
				if m.Internal && strings.HasPrefix(strings.TrimSpace(b.Text), compactedHeader) {
					ws.carried = carriedBody(b.Text)
				} else if m.Role == RoleUser && !m.Internal && !m.HasToolResults() && strings.TrimSpace(b.Text) != "" {
					ws.requests = append(ws.requests, clipRunes(strings.TrimSpace(b.Text), maxRequestChars))
					if len(ws.requests) > maxWorkingSetRequests {
						ws.requests = ws.requests[1:]
					}
				}
			case BlockToolUse:
				calls[b.ID] = b
			case BlockToolResult:
				call, ok := calls[b.ToolUseID]
				if !ok {
					continue
				}
				body := blockText(b.Content)
				if b.IsError {
					ws.failure = fmt.Sprintf("%s failed\n%s", call.Name, tailRunes(body, maxFailureChars))
				} else {
					if isTodoTool(call.Name) {
						ws.todo = clipRunes(strings.TrimSpace(string(call.Input)), maxTodoChars)
					}
					if path := inputPath(call.Input); path != "" && ClassifyTool(call.Name) == ToolMutate {
						ws.edited = recentUnique(ws.edited, path, maxWorkingSetFiles)
					}
				}
				switch ClassifyTool(call.Name) {
				case ToolVerify:
					cmd := inputCommand(call.Input)
					if cmd == "" {
						cmd = call.Name
					}
					exit := exitCodeLabel(body, b.IsError)
					ws.commands = appendRecent(ws.commands, commandNote{command: cmd, exit: exit}, maxWorkingSetCommands)
					if exit != "0" && exit != "unknown" {
						ws.failure = fmt.Sprintf("`%s` exit %s\n%s", cmd, exit, clipRunes(tailRunes(body, maxFailureChars), maxFailureChars))
					}
				case ToolExplore:
					if b.IsError {
						continue
					}
					path := inputPath(call.Input)
					if path == "" {
						continue
					}
					note := fileNote{path: path, hash: hashPrefix(body)}
					for i, previous := range ws.read {
						if previous.path == path {
							ws.read = append(ws.read[:i], ws.read[i+1:]...)
							break
						}
					}
					ws.read = appendRecent(ws.read, note, maxWorkingSetFiles)
				}
			}
		}
	}
	return ws
}

func appendRecent[T any](items []T, value T, limit int) []T {
	items = append(items, value)
	if len(items) > limit {
		items = items[len(items)-limit:]
	}
	return items
}

func recentUnique(items []string, value string, limit int) []string {
	for i, previous := range items {
		if previous == value {
			items = append(items[:i], items[i+1:]...)
			break
		}
	}
	return appendRecent(items, value, limit)
}

func (w workingSet) render(dropped int) string {
	var b strings.Builder
	fmt.Fprintf(&b, "%s %d earlier messages were summarized to fit the context window. "+
		"The goal above and the most recent messages are intact; nothing below has been undone. "+
		"Do not restart or re-read what is already recorded here.\n", compactedHeader, dropped)
	if w.carried != "" {
		b.WriteString("\nCarried forward from an earlier compaction:\n")
		b.WriteString(w.carried)
		b.WriteByte('\n')
	}
	if len(w.requests) > 0 {
		b.WriteString("\nEarlier user requests and corrections (in chronological order):\n")
		for _, request := range w.requests {
			fmt.Fprintf(&b, "- %s\n", request)
		}
	}
	if len(w.read) > 0 {
		b.WriteString("\nFiles read:\n")
		for _, f := range w.read {
			fmt.Fprintf(&b, "- %s (sha %s)\n", f.path, f.hash)
		}
	}
	if len(w.edited) > 0 {
		b.WriteString("\nFiles changed:\n")
		for _, p := range w.edited {
			fmt.Fprintf(&b, "- %s\n", p)
		}
	}
	if len(w.commands) > 0 {
		b.WriteString("\nCommands run:\n")
		for _, c := range w.commands {
			fmt.Fprintf(&b, "- %s → exit %s\n", c.command, c.exit)
		}
	}
	if w.failure != "" {
		b.WriteString("\nLast failing check:\n")
		b.WriteString(w.failure)
		b.WriteByte('\n')
	}
	if w.todo != "" {
		b.WriteString("\nTODO state:\n")
		b.WriteString(w.todo)
		b.WriteByte('\n')
	}
	if len(w.decisions) > 0 {
		b.WriteString("\nDecisions so far:\n")
		for _, d := range w.decisions {
			fmt.Fprintf(&b, "- %s\n", d)
		}
	}
	return b.String()
}

// carriedBody strips the explanatory header from a previous compaction message
// and bounds what is carried into the next one.
func carriedBody(text string) string {
	body := strings.TrimSpace(text)
	if idx := strings.Index(body, "\n"); idx >= 0 {
		body = strings.TrimSpace(body[idx+1:])
	}
	return clipRunes(body, maxCarriedChars)
}

func isTodoTool(name string) bool {
	n := strings.ToLower(strings.TrimSpace(name))
	return n == "todo" || strings.HasPrefix(n, "todo.") || strings.HasPrefix(n, "plan.")
}

// inputPath pulls the file path out of a tool input object.
func inputPath(input json.RawMessage) string {
	var args map[string]any
	if len(input) == 0 || json.Unmarshal(input, &args) != nil {
		return ""
	}
	for _, key := range []string{"path", "file_path", "file", "target"} {
		if v, ok := args[key].(string); ok && strings.TrimSpace(v) != "" {
			return strings.TrimSpace(v)
		}
	}
	return ""
}

// inputCommand renders the command a verify/shell tool ran.
func inputCommand(input json.RawMessage) string {
	var args map[string]any
	if len(input) == 0 || json.Unmarshal(input, &args) != nil {
		return ""
	}
	if v, ok := args["command"].(string); ok && strings.TrimSpace(v) != "" {
		return clipRunes(strings.TrimSpace(v), 200)
	}
	if raw, ok := args["argv"].([]any); ok && len(raw) > 0 {
		parts := make([]string, 0, len(raw))
		for _, item := range raw {
			if s, ok := item.(string); ok {
				parts = append(parts, s)
			}
		}
		return clipRunes(strings.Join(parts, " "), 200)
	}
	return ""
}

// exitCodeLabel reads exit_code out of a tool result body, falling back to the
// error flag when the tool does not report one.
func exitCodeLabel(body string, isError bool) string {
	var out struct {
		ExitCode *int `json:"exit_code"`
		TimedOut bool `json:"timed_out"`
	}
	if json.Unmarshal([]byte(body), &out) == nil && out.ExitCode != nil && *out.ExitCode != 0 {
		return fmt.Sprintf("%d", *out.ExitCode)
	}
	if isError || out.TimedOut {
		return "err"
	}
	if resultLooksGreen(ToolResult{Output: []byte(body)}) {
		return "0"
	}
	return "unknown"
}

// resultLooksGreen reports a verify result that actually passed: no tool error
// and an explicit completed exit code 0.
func resultLooksGreen(res ToolResult) bool {
	if res.Err != "" || res.IsError {
		return false
	}
	var body struct {
		ExitCode *int   `json:"exit_code"`
		TimedOut bool   `json:"timed_out"`
		Status   string `json:"status"`
	}
	if json.Unmarshal(res.Output, &body) != nil || body.ExitCode == nil || body.TimedOut {
		return false
	}
	// A successful background launch is not a completed verification.
	switch strings.ToLower(strings.TrimSpace(body.Status)) {
	case "", "completed", "exited", "done", "ok", "success":
		return *body.ExitCode == 0
	default:
		return false
	}
}

func blockText(blocks []Block) string {
	var b strings.Builder
	for _, block := range blocks {
		switch block.Type {
		case BlockText:
			b.WriteString(block.Text)
		case BlockImage:
			b.WriteString("[image ")
			b.WriteString(block.MediaType)
			b.WriteString("]")
		}
	}
	return b.String()
}

func hashPrefix(body string) string {
	sum := sha256.Sum256([]byte(body))
	return hex.EncodeToString(sum[:4])
}

// firstSentence is the decision line of an assistant round: its first sentence,
// bounded so a compaction stays small.
func firstSentence(text string) string {
	t := strings.TrimSpace(text)
	if t == "" {
		return ""
	}
	if idx := strings.IndexAny(t, ".!?\n"); idx > 0 {
		t = t[:idx+1]
	}
	return clipRunes(strings.TrimSpace(strings.ReplaceAll(t, "\n", " ")), 200)
}

func clipRunes(s string, max int) string {
	if max <= 0 {
		return ""
	}
	runes := []rune(s)
	if len(runes) <= max {
		return s
	}
	return string(runes[:max]) + "…"
}

func tailRunes(s string, max int) string {
	runes := []rune(s)
	if max <= 0 || len(runes) <= max {
		return s
	}
	return string(runes[len(runes)-max:])
}
