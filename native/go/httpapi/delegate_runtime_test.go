package httpapi

import (
	"context"
	"encoding/json"
	"path/filepath"
	"strings"
	"sync"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// The delegate tool hands a mission to another agent. The runtime's half of
// that is: the owner is told plainly what they are approving, the sub-agent is
// bound to the session's folder, its live events reach the trail labelled as
// its own work, and the turn does not time it out mid-build.

func TestDelegateApprovalSummaryNamesTheAgentAndTheFolder(t *testing.T) {
	folder := filepath.FromSlash("C:/work/remedy")
	call := cognition.ToolCall{Name: "delegate", Input: []byte(`{"mission":"port the parser","allow_shell":true}`)}

	summary := plainToolApprovalSummary(call.Name, toolCommandPreview(call), folder)
	for _, want := range []string{"Claude Code", folder, "file edits and shell commands"} {
		if !strings.Contains(summary, want) {
			t.Fatalf("the banner must say %q: %q", want, summary)
		}
	}

	call.Input = []byte(`{"mission":"port the parser","cwd":"C:/work/remedy/parser"}`)
	summary = plainToolApprovalSummary(call.Name, toolCommandPreview(call), folder)
	if !strings.Contains(summary, "C:/work/remedy/parser") {
		t.Fatalf("the banner must name the folder the agent was given: %q", summary)
	}
	if !strings.Contains(summary, "no shell commands") {
		t.Fatalf("the banner must say the shell is off by default: %q", summary)
	}
}

func TestDelegateIsBoundToTheSessionFolder(t *testing.T) {
	root := filepath.FromSlash("C:/work/remedy")
	call := bindToolInput(cognition.ToolCall{
		Name:  "delegate",
		Input: []byte(`{"mission":"do it","workspace_root":"C:/somewhere/else","write_roots":["C:/"]}`),
	}, toolBinding{Root: root, Scope: "project", HomeDir: t.TempDir(), SessionID: "s1"})

	var body struct {
		Mission       string   `json:"mission"`
		WorkspaceRoot string   `json:"workspace_root"`
		WriteRoots    []string `json:"write_roots"`
	}
	if err := json.Unmarshal(call.Input, &body); err != nil {
		t.Fatal(err)
	}
	if body.Mission != "do it" {
		t.Fatalf("the mission must survive binding: %#v", body)
	}
	if body.WorkspaceRoot != root {
		t.Fatalf("a model-supplied root must be overwritten: %q", body.WorkspaceRoot)
	}
	if len(body.WriteRoots) != 1 || body.WriteRoots[0] != root {
		t.Fatalf("the sub-agent spawns under the session write jail: %v", body.WriteRoots)
	}
}

func TestDelegateOwnsItsOwnTimeout(t *testing.T) {
	if d := deadlineForTool("delegate"); d != 0 {
		t.Fatalf("a delegated mission must not be cut off by the runtime deadline: %s", d)
	}
}

func TestProgressTokensCarryTheSubAgentLabel(t *testing.T) {
	status := formatProgressToken(tools.ProgressEvent{
		Kind: tools.ProgressStatus, Via: "claude-code", Text: "Reading main.go\nbefore I change it.",
	})
	if status != "@@status:Reading main.go before I change it.\n" {
		t.Fatalf("status token=%q", status)
	}
	if formatProgressToken(tools.ProgressEvent{Kind: tools.ProgressStatus, Via: "claude-code", Text: "  "}) != "" {
		t.Fatal("an empty status must not reach the stream")
	}

	callToken := formatProgressToken(tools.ProgressEvent{
		Kind: tools.ProgressToolCall, Via: "claude-code", Name: "Edit", CallID: "toolu_1",
		Input: json.RawMessage(`{"file_path":"main.go"}`),
	})
	if !strings.HasPrefix(callToken, "@@tool_call:") || !strings.HasSuffix(callToken, "\n") {
		t.Fatalf("tool call token=%q", callToken)
	}
	var call map[string]any
	if err := json.Unmarshal([]byte(strings.TrimPrefix(strings.TrimSpace(callToken), "@@tool_call:")), &call); err != nil {
		t.Fatal(err)
	}
	if call["via"] != "claude-code" || call["id"] != "toolu_1" || call["name"] != "claude-code:Edit" {
		t.Fatalf("a sub-agent's call must be labelled as its own: %#v", call)
	}
	if args, _ := call["args"].(map[string]any); args["file_path"] != "main.go" {
		t.Fatalf("the sub-agent's arguments must survive: %#v", call)
	}

	resultToken := formatProgressToken(tools.ProgressEvent{
		Kind: tools.ProgressToolResult, Via: "claude-code", Name: "Edit", CallID: "toolu_1",
		Output: "main.go updated", IsError: true,
	})
	var res map[string]any
	if err := json.Unmarshal([]byte(strings.TrimPrefix(strings.TrimSpace(resultToken), "@@tool_result:")), &res); err != nil {
		t.Fatal(err)
	}
	if res["via"] != "claude-code" || res["ok"] != false || res["output"] != "main.go updated" {
		t.Fatalf("tool result token=%#v", res)
	}
}

// progressingTool reports mid-execution, the way delegate does while its
// sub-agent works.
type progressingTool struct{}

func (progressingTool) Execute(ctx context.Context, call cognition.ToolCall) cognition.ToolResult {
	tools.EmitProgress(ctx, tools.ProgressEvent{Kind: tools.ProgressStatus, Via: "claude-code", Text: "working"})
	tools.EmitProgress(ctx, tools.ProgressEvent{Kind: tools.ProgressToolCall, Via: "claude-code", Name: "Edit", CallID: "toolu_1"})
	return cognition.ToolResult{ID: call.ID, Name: call.Name, Output: []byte(`{"report":"done"}`)}
}

func TestRunnerStreamsToolProgressAsItHappens(t *testing.T) {
	var (
		mu     sync.Mutex
		tokens []string
	)
	emitting := &emittingTools{inner: progressingTool{}, emit: func(tok string) {
		mu.Lock()
		defer mu.Unlock()
		tokens = append(tokens, tok)
	}}
	res := emitting.Execute(context.Background(), cognition.ToolCall{ID: "c1", Name: "delegate"})
	if res.Err != "" {
		t.Fatalf("tool failed: %s", res.Err)
	}
	mu.Lock()
	defer mu.Unlock()
	if len(tokens) != 3 {
		t.Fatalf("progress must reach the stream before the result: %#v", tokens)
	}
	if tokens[0] != "@@status:working\n" {
		t.Fatalf("first token=%q", tokens[0])
	}
	if !strings.HasPrefix(tokens[1], "@@tool_call:") || !strings.Contains(tokens[1], `"via":"claude-code"`) {
		t.Fatalf("second token=%q", tokens[1])
	}
	if !strings.HasPrefix(tokens[2], "@@tool_result:") || !strings.Contains(tokens[2], `"name":"delegate"`) {
		t.Fatalf("last token must be the tool's own result: %q", tokens[2])
	}
}
