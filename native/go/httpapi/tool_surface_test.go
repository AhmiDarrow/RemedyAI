package httpapi

import (
	"context"
	"encoding/json"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// The frontier tool surface: the names a model sees, what the runtime binds
// underneath them, and the policy each one gets.

func decodeInput(t *testing.T, raw []byte) map[string]any {
	t.Helper()
	out := map[string]any{}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatalf("bound input is not an object: %v %s", err, raw)
	}
	return out
}

func TestFrontierToolsAreRegisteredAndAdvertised(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"read", "edit", "write", "glob", "grep", "bash", "jobs", "todo", "screenshot", "delegate"} {
		desc, err := reg.Latest(id)
		if err != nil {
			t.Fatalf("%s must be registered: %v", id, err)
		}
		if !modelVisibleTool(desc.ID) {
			t.Fatalf("%s must be advertised to the model", id)
		}
		if strings.TrimSpace(desc.Description) == "" {
			t.Fatalf("%s has no description", id)
		}
	}
	// The coding pack is the mid-build surface: the file tools, the shell and
	// its jobs, the checklist and the hand-off. screenshot is not coding work.
	for _, id := range []string{"read", "edit", "write", "glob", "grep", "bash", "jobs", "todo", "delegate"} {
		if !isCodingPackTool(id) {
			t.Fatalf("%s must survive the mid-build coding pack", id)
		}
	}
	// The ids the surface replaced stay registered — approvals are
	// fingerprinted on the tool identity and the CLI still calls them.
	for _, id := range []string{"shell.exec"} {
		if _, err := reg.Latest(id); err != nil {
			t.Fatalf("%s must stay registered: %v", id, err)
		}
		if modelVisibleTool(id) {
			t.Fatalf("%s must be hidden from the model", id)
		}
	}
}

func TestBindToolInputBindsTheFrontierTools(t *testing.T) {
	b := toolBinding{Root: `C:\projects\app`, Scope: "project", HomeDir: `C:\home\.remedy`, SessionID: "s-42"}

	// File tools: the root is the runtime's, never the model's.
	for _, name := range []string{"read", "edit", "write", "glob", "grep"} {
		call := bindToolInput(cognition.ToolCall{
			Name:  name,
			Input: []byte(`{"path":"a.txt","workspace_root":"C:\\evil","home_dir":"C:\\evil"}`),
		}, b)
		args := decodeInput(t, call.Input)
		if args["workspace_root"] != b.Root {
			t.Fatalf("%s workspace_root=%v", name, args["workspace_root"])
		}
		if _, ok := args["home_dir"]; ok {
			t.Fatalf("%s must not receive home_dir: %#v", name, args)
		}
	}

	// bash: cwd clamped, write jail bound, session bound for background jobs.
	call := bindToolInput(cognition.ToolCall{
		Name:  "bash",
		Input: []byte(`{"command":"go test ./...","cwd":"C:\\Windows\\System32","write_roots":["C:\\"],"session_id":"spoofed","home_dir":"C:\\evil"}`),
	}, b)
	args := decodeInput(t, call.Input)
	if args["cwd"] != b.Root {
		t.Fatalf("bash cwd=%v", args["cwd"])
	}
	roots, _ := args["write_roots"].([]any)
	if len(roots) != 1 || roots[0] != b.Root {
		t.Fatalf("bash write_roots=%#v", args["write_roots"])
	}
	if args["session_id"] != "s-42" || args["home_dir"] != b.HomeDir {
		t.Fatalf("bash session binding=%#v", args)
	}
	if args["command"] != "go test ./..." {
		t.Fatalf("bash command must survive binding: %#v", args)
	}

	// Session tools carry the session, and nothing else the model chose.
	for _, name := range []string{"jobs", "todo"} {
		call := bindToolInput(cognition.ToolCall{
			Name:  name,
			Input: []byte(`{"action":"list","session_id":"spoofed","home_dir":"C:\\evil"}`),
		}, b)
		args := decodeInput(t, call.Input)
		if args["session_id"] != "s-42" || args["home_dir"] != b.HomeDir {
			t.Fatalf("%s binding=%#v", name, args)
		}
	}

	// With no session, the spoofed values are removed rather than trusted.
	call = bindToolInput(cognition.ToolCall{
		Name:  "todo",
		Input: []byte(`{"items":[],"session_id":"spoofed","home_dir":"C:\\evil"}`),
	}, toolBinding{Root: b.Root})
	args = decodeInput(t, call.Input)
	if _, ok := args["session_id"]; ok {
		t.Fatalf("an unbound session must not fall back to the model's: %#v", args)
	}
	if _, ok := args["home_dir"]; ok {
		t.Fatalf("an unbound home must not fall back to the model's: %#v", args)
	}
}

func TestJobsListAndTailReadWhileKillAsks(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	q := newApprovalQueue()
	q.SetMode("ask")
	policy := &RegistryPolicy{Registry: reg, Approvals: q, SessionID: "s1"}
	ctx := context.Background()

	for _, action := range []string{"list", "tail"} {
		call := cognition.ToolCall{Name: "jobs", Input: []byte(`{"action":"` + action + `","job_id":"job_1"}`)}
		if got := policy.Decide(ctx, call); got != cognition.Allow {
			t.Fatalf("jobs %s must not ask the owner: %v", action, got)
		}
	}
	kill := cognition.ToolCall{Name: "jobs", Input: []byte(`{"action":"kill","job_id":"job_1"}`)}
	if got := policy.Decide(ctx, kill); got != cognition.Ask {
		t.Fatalf("jobs kill decision=%v want Ask", got)
	}
	// A malformed jobs call is never treated as read-only.
	bad := cognition.ToolCall{Name: "jobs", Input: []byte(`not json`)}
	if got := policy.Decide(ctx, bad); got != cognition.Ask {
		t.Fatalf("malformed jobs decision=%v want Ask", got)
	}
	// The file mutations still ask, and the readers still do not.
	for _, name := range []string{"edit", "write", "bash", "todo"} {
		if got := policy.Decide(ctx, cognition.ToolCall{Name: name, Input: []byte(`{}`)}); got != cognition.Ask {
			t.Fatalf("%s decision=%v want Ask", name, got)
		}
	}
	for _, name := range []string{"read", "glob", "grep", "screenshot"} {
		if got := policy.Decide(ctx, cognition.ToolCall{Name: name, Input: []byte(`{}`)}); got != cognition.Allow {
			t.Fatalf("%s decision=%v want Allow", name, got)
		}
	}
}

func TestBashKeepsTheSensitiveCommandPredicate(t *testing.T) {
	money := cognition.ToolCall{Name: "bash", Input: []byte(`{"command":"stripe payment_intents create --amount 5000"}`)}
	if !approvalIsSensitive(money) {
		t.Fatal("a money command through bash must be an owner moment")
	}
	creds := cognition.ToolCall{Name: "bash", Input: []byte(`{"command":"aws configure set aws_secret_access_key abc"}`)}
	if !approvalIsSensitive(creds) {
		t.Fatal("a credential command through bash must be an owner moment")
	}
	plain := cognition.ToolCall{Name: "bash", Input: []byte(`{"command":"go test ./..."}`)}
	if approvalIsSensitive(plain) {
		t.Fatal("an ordinary build command is not an owner moment")
	}
	// Auto mode does not waive the owner moment.
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	q := newApprovalQueue()
	q.SetMode("auto")
	policy := &RegistryPolicy{Registry: reg, Approvals: q, SessionID: "s1"}
	if got := policy.Decide(context.Background(), money); got != cognition.Ask {
		t.Fatalf("auto mode waived a payment command: %v", got)
	}
	if got := policy.Decide(context.Background(), plain); got != cognition.Allow {
		t.Fatalf("auto mode must run ordinary commands: %v", got)
	}
}

func TestToolResultCarriesImageBlocksIntoTheTranscript(t *testing.T) {
	reg := tools.NewRegistry(tools.AuthorizerFunc(func(context.Context, tools.Descriptor, tools.Request) error { return nil }))
	png := []byte{0x89, 'P', 'N', 'G'}
	err := reg.Register(tools.Descriptor{
		ID: "surface.image", Version: 1, Description: "test image tool",
		Runtime:      tools.RuntimeGo,
		Risk:         tools.RiskReadOnly,
		InputSchema:  json.RawMessage(`{"type":"object","additionalProperties":false}`),
		OutputSchema: json.RawMessage(`{"type":"object","required":["path"],"properties":{"path":{"type":"string"}},"additionalProperties":false}`),
	}, tools.ExecutorFunc(func(context.Context, tools.Request) (tools.Result, error) {
		return tools.Result{
			Output: json.RawMessage(`{"path":"shot.png"}`),
			Images: []tools.ImageResult{{MediaType: "image/png", Data: png}},
		}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	exec := &RegistryToolExecutor{Registry: reg, TokenFor: RuntimeCapabilityToken}
	res := exec.Execute(context.Background(), cognition.ToolCall{ID: "c1", Name: "surface.image"})
	if res.Err != "" {
		t.Fatalf("err=%s", res.Err)
	}
	if len(res.Blocks) != 1 || res.Blocks[0].Type != cognition.BlockImage {
		t.Fatalf("blocks=%#v", res.Blocks)
	}
	if res.Blocks[0].MediaType != "image/png" || string(res.Blocks[0].Data) != string(png) {
		t.Fatalf("image block=%#v", res.Blocks[0])
	}
	// The bytes are not also in the JSON body — that would charge the model twice.
	if strings.Contains(string(res.Output), "PNG") {
		t.Fatalf("image bytes leaked into the text output: %s", res.Output)
	}
}

func TestTodoResultEmitsTheTodosToken(t *testing.T) {
	res := cognition.ToolResult{
		Name:   "todo",
		Output: []byte(`{"todos":[{"id":"a","content":"ship it","status":"in_progress"}],"open":1,"path":"x"}`),
	}
	tok := todosToken(res)
	if !strings.HasPrefix(tok, "@@todos:") || !strings.HasSuffix(tok, "\n") {
		t.Fatalf("token=%q", tok)
	}
	var payload map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSuffix(strings.TrimPrefix(tok, "@@todos:"), "\n")), &payload); err != nil {
		t.Fatal(err)
	}
	if payload["type"] != "todos" || payload["open"] != float64(1) {
		t.Fatalf("payload=%#v", payload)
	}
	rows, _ := payload["todos"].([]any)
	if len(rows) != 1 {
		t.Fatalf("todos=%#v", payload["todos"])
	}
	row, _ := rows[0].(map[string]any)
	if row["content"] != "ship it" || row["status"] != "in_progress" {
		t.Fatalf("row=%#v", row)
	}

	// A failed or unrelated call emits nothing.
	if tok := todosToken(cognition.ToolResult{Name: "todo", Err: "no session"}); tok != "" {
		t.Fatalf("failed todo emitted %q", tok)
	}
	if tok := todosToken(cognition.ToolResult{Name: "read", Output: []byte(`{"todos":[]}`)}); tok != "" {
		t.Fatalf("read emitted %q", tok)
	}
}

func TestApprovalSummaryNamesTheFrontierTools(t *testing.T) {
	cases := map[string]string{
		"edit":     "change a file",
		"write":    "change a file",
		"bash":     "run a command",
		"jobs":     "stop a background command",
		"todo":     "task checklist",
		"delegate": "Claude Code",
	}
	for name, want := range cases {
		got := plainToolApprovalSummary(name, `{"command":"go test ./..."}`, "")
		if !strings.Contains(got, want) {
			t.Fatalf("%s summary=%q want to mention %q", name, got, want)
		}
	}
}
