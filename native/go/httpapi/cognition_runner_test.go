package httpapi

import (
	"context"
	"errors"
	"net"
	"os"
	"path/filepath"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/ipc"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func TestLastResultsInputSurfacesExitCode(t *testing.T) {
	rows := lastResultsInput([]cognition.ToolResult{{
		Name:   "bash",
		Output: []byte(`{"command":"pytest","exit_code":1,"stdout":"` + strings.Repeat("x", 800) + `"}`),
	}})
	if len(rows) != 1 {
		t.Fatalf("rows=%d", len(rows))
	}
	if rows[0]["ok"] != false {
		t.Fatalf("a non-zero exit must not look successful: %v", rows[0])
	}
	if rows[0]["exit_code"] != 1 {
		t.Fatalf("exit_code=%v", rows[0]["exit_code"])
	}
	tail, _ := rows[0]["tail"].(string)
	if !strings.HasPrefix(tail, "exit_code=1 ") {
		t.Fatalf("tail must lead with the exit code, got %q", tail)
	}
}

func TestCognitionTurnRunnerEmitsTextAndCompletes(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{Text: "Hello ", Done: false}, {Text: "world", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "hi"})
	if err != nil {
		t.Fatal(err)
	}
	if out != "Hello world" {
		t.Fatalf("got %q", out)
	}
	if model.LastTurn.System != "" {
		t.Fatalf("raw path must not inject system without worker, got %q", model.LastTurn.System)
	}
}

func TestCognitionTurnRunnerAssemblesPromptOverRMDY(t *testing.T) {
	serverReg := tools.NewRegistry()
	if err := tools.RegisterPythonWorkerLocalMirrors(serverReg); err != nil {
		t.Fatal(err)
	}
	clientConn, serverConn := net.Pipe()
	defer clientConn.Close()
	defer serverConn.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go ipc.ServeConn(ctx, serverConn, tools.WorkerHandler{Registry: serverReg})
	client := ipc.NewClient(clientConn)
	defer client.Close()

	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{Text: "assembled-ok", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	if err := r.AttachPythonWorker(client); err != nil {
		t.Fatal(err)
	}
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "hello partner", SessionID: "s1"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "assembled-ok") {
		t.Fatalf("missing final text: %q", out)
	}
	if !strings.Contains(model.LastTurn.System, "mirror-system") {
		t.Fatalf("expected assembled system on model turn, got %q", model.LastTurn.System)
	}
	if got := model.LastTurn.FirstUserText(); got != "hello partner" {
		t.Fatalf("user message=%q", got)
	}
	if _, err := r.Registry.Latest("prompt.assemble"); err != nil {
		t.Fatalf("prompt.assemble missing after attach: %v", err)
	}
}

func TestCognitionTurnRunnerFailsClosedWhenAssembleUnavailable(t *testing.T) {
	// Empty server registry — worker has no prompt.assemble handler.
	serverReg := tools.NewRegistry()
	clientConn, serverConn := net.Pipe()
	defer clientConn.Close()
	defer serverConn.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go ipc.ServeConn(ctx, serverConn, tools.WorkerHandler{Registry: serverReg})
	client := ipc.NewClient(clientConn)
	defer client.Close()

	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{Text: "should-not-run", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	if err := r.AttachPythonWorker(client); err != nil {
		t.Fatal(err)
	}
	_, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "hi"})
	if err == nil {
		t.Fatal("expected fail-closed when prompt.assemble unavailable")
	}
	if !strings.Contains(err.Error(), "prompt.assemble") {
		t.Fatalf("error should mention prompt.assemble, got %v", err)
	}
	if model.LastTurn.System != "" || len(model.LastTurn.Messages) != 0 {
		t.Fatalf("model must not see the turn when assemble fails: %+v", model.LastTurn)
	}
}

func TestCognitionTurnRunnerRegistersZigHostTools(t *testing.T) {
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	for _, id := range []string{
		"computer.screenshot", "computer.windows", "computer.monitors", "computer.snapshot",
		"computer.click", "computer.type", "computer.key", "computer.key_hold", "computer.move", "computer.scroll", "computer.drag",
		"computer.focus", "computer.window",
		"clipboard.read", "clipboard.read_files", "clipboard.read_image", "clipboard.write", "shell.exec",
	} {
		desc, err := r.Registry.Latest(id)
		if err != nil {
			t.Fatalf("%s missing: %v", id, err)
		}
		if desc.Runtime != tools.RuntimeZig {
			t.Fatalf("%s runtime=%s", id, desc.Runtime)
		}
	}
}

func TestCognitionTurnRunnerExecutesRealGoBuiltinTools(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "1", Name: "runtime.probe", Input: []byte(`{}`)}},
			{ToolCall: &cognition.ToolCall{ID: "2", Name: "text.sha256", Input: []byte(`{"text":"hi"}`)}},
			{ToolCall: &cognition.ToolCall{ID: "3", Name: "json.canonical", Input: []byte(`{"value":{"z":1,"a":2}}`)}}},
		{{Text: "done", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "probe"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, `@@tool_call:`) || !strings.Contains(out, `"runtime.probe"`) {
		t.Fatalf("missing tool_call: %q", out)
	}
	if !strings.Contains(out, `@@tool_result:`) || !strings.Contains(out, `"ok":true`) {
		t.Fatalf("missing tool_result: %q", out)
	}
	if !strings.Contains(out, `"status":"ready"`) && !strings.Contains(out, `"status\": \"ready\"`) {
		// preview is raw JSON output of the tool
		if !strings.Contains(out, "ready") || !strings.Contains(out, "tool_abi") {
			t.Fatalf("missing probe payload in tool_result: %q", out)
		}
	}
	if !strings.Contains(out, "sha256") {
		t.Fatalf("missing sha256 tool_result: %q", out)
	}
	if !strings.Contains(out, "done") {
		t.Fatalf("missing final text: %q", out)
	}
}

func TestCognitionTurnRunnerExecutesPythonToolsOverRMDY(t *testing.T) {
	serverReg := tools.NewRegistry()
	if err := tools.RegisterPythonWorkerLocalMirrors(serverReg); err != nil {
		t.Fatal(err)
	}
	clientConn, serverConn := net.Pipe()
	defer clientConn.Close()
	defer serverConn.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go ipc.ServeConn(ctx, serverConn, tools.WorkerHandler{Registry: serverReg})
	client := ipc.NewClient(clientConn)
	defer client.Close()

	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "1", Name: "text.slugify", Input: []byte(`{"text":"Hello Worker"}`)}}},
		{{Text: "slug-ok", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	if err := r.AttachPythonWorker(client); err != nil {
		t.Fatal(err)
	}
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "slug"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "text.slugify") || !strings.Contains(out, `"ok":true`) {
		t.Fatalf("missing python tool result: %q", out)
	}
	if !strings.Contains(out, "hello-worker") {
		t.Fatalf("missing slug preview: %q", out)
	}
	if !strings.Contains(out, "slug-ok") {
		t.Fatalf("missing final text: %q", out)
	}
}

func TestCognitionTurnRunnerExecutesWorkspaceListOverRMDY(t *testing.T) {
	serverReg := tools.NewRegistry()
	if err := tools.RegisterPythonWorkerLocalMirrors(serverReg); err != nil {
		t.Fatal(err)
	}
	clientConn, serverConn := net.Pipe()
	defer clientConn.Close()
	defer serverConn.Close()
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	go ipc.ServeConn(ctx, serverConn, tools.WorkerHandler{Registry: serverReg})
	client := ipc.NewClient(clientConn)
	defer client.Close()

	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "1", Name: "workspace.list", Input: []byte(`{"path":"."}`)}}},
		{{Text: "listed", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	if err := r.AttachPythonWorker(client); err != nil {
		t.Fatal(err)
	}
	if _, err := r.Registry.Latest("workspace.read"); err != nil {
		t.Fatalf("workspace.read missing after attach: %v", err)
	}
	if _, err := r.Registry.Latest("memory.search"); err != nil {
		t.Fatalf("memory.search missing after attach: %v", err)
	}
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "list"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "workspace.list") || !strings.Contains(out, `"ok":true`) {
		t.Fatalf("missing workspace.list result: %q", out)
	}
	if !strings.Contains(out, "mirror.txt") {
		t.Fatalf("missing list preview: %q", out)
	}
	if !strings.Contains(out, "listed") {
		t.Fatalf("missing final text: %q", out)
	}
}

func TestCognitionTurnRunnerDeniesUnregisteredTools(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "1", Name: "file_read", Input: []byte(`{"path":"a.py"}`)}}},
		{{Text: "after-deny", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "read"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, `@@tool_call:`) || !strings.Contains(out, "file_read") {
		t.Fatalf("expected tool_call for unregistered tool, got %q", out)
	}
	// Deny skips Execute, so no successful tool_result token is emitted.
	if strings.Contains(out, `"ok":true`) {
		t.Fatalf("unregistered tool must not execute, got %q", out)
	}
	if !strings.Contains(out, "after-deny") {
		t.Fatalf("missing final text: %q", out)
	}
}

func TestCognitionTurnRunnerEmitsToolCallThenResultWhenAllowed(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "1", Name: "file_read", Input: []byte(`{"path":"a.py"}`)}}},
		{{Text: "done", Done: true}},
	}}
	r := &CognitionTurnRunner{
		Model:  model,
		Tools:  cognition.EchoTools{},
		Policy: cognition.AllowAll{},
	}
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "read"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, `@@tool_call:`) || !strings.Contains(out, `"file_read"`) {
		t.Fatalf("missing tool_call: %q", out)
	}
	if !strings.Contains(out, `@@tool_result:`) || !strings.Contains(out, `"ok":true`) {
		t.Fatalf("missing tool_result: %q", out)
	}
	if !strings.Contains(out, "done") {
		t.Fatalf("missing final text: %q", out)
	}
}

func TestCognitionTurnRunnerAbortEmitsControlToken(t *testing.T) {
	model := cognition.Model(cognitionModelFunc(func(ctx context.Context, _ cognition.Turn) (<-chan cognition.ModelEvent, error) {
		ch := make(chan cognition.ModelEvent)
		go func() {
			defer close(ch)
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
				ch <- cognition.ModelEvent{Text: "late", Done: true}
			}
		}()
		return ch, nil
	}))
	r := NewCognitionTurnRunner(model)
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(20 * time.Millisecond)
		cancel()
	}()
	out, err := CollectTokens(ctx, r, TurnRequest{Prompt: "x"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "@@aborted") {
		t.Fatalf("expected @@aborted, got %q", out)
	}
}

func TestCognitionTurnRunnerFallsBackOnProvider402(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("REMEDY_XAI_API_KEY", "")
	t.Setenv("OPENAI_API_KEY", "")
	t.Setenv("REMEDY_OPENAI_API_KEY", "")
	t.Setenv("ANTHROPIC_API_KEY", "")
	InvalidateConfigCache()
	_ = os.MkdirAll(filepath.Join(home, "auth"), 0o700)

	failing := cognitionModelFunc(func(context.Context, cognition.Turn) (<-chan cognition.ModelEvent, error) {
		return nil, errors.New(`openai-compat HTTP 402: {"error":{"message":"The model assistant requires an active Poe subscription for API access."}}`)
	})
	// No cloud credentials → resolveChatModel(exclude=poe) → Scripted Hello world.
	prov := "poe"
	r := NewCognitionTurnRunner(nil)
	r.HomeDir = home
	r.forcePrimary = failing
	out, err := CollectTokens(context.Background(), r, TurnRequest{
		Prompt:   "hi",
		Provider: &prov,
	})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "That provider isn't available") {
		t.Fatalf("expected switch status, got %q", out)
	}
	if !strings.Contains(out, "Hello world") {
		t.Fatalf("expected scripted fallback text, got %q", out)
	}
}

func TestCognitionTurnRunnerOwnerCheckpointStatus(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "pay", Name: "payment.submit", Input: []byte(`{}`)}}},
	}}
	r := &CognitionTurnRunner{
		Model: model,
		Policy: cognition.Policy(cognitionPolicyFunc(func(context.Context, cognition.ToolCall) cognition.Decision {
			return cognition.Ask
		})),
		Tools: cognition.EchoTools{},
	}
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "pay"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "@@tool_call:") || !strings.Contains(out, "payment.submit") {
		t.Fatalf("pending tool not emitted: %q", out)
	}
	if !strings.Contains(out, "@@status:Waiting for your approval") {
		t.Fatalf("missing approval status: %q", out)
	}
}

func TestApproveResumesPendingToolBatch(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	q := newApprovalQueue()
	_ = q.SetMode("ask")
	input := `{"argv":["C:\\Windows\\System32\\cmd.exe","/c","echo"]}`
	var executed atomic.Int32
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "1", Name: "shell.exec", Input: []byte(input)}}},
		{{Text: "finished", Done: true}},
	}}
	r := &CognitionTurnRunner{
		Model:     model,
		Registry:  reg,
		Approvals: q,
		Policy:    &RegistryPolicy{Registry: reg, Approvals: q, SessionID: "sess-resume-approve"},
		Tools: cognitionToolFunc(func(_ context.Context, call cognition.ToolCall) cognition.ToolResult {
			executed.Add(1)
			return cognition.ToolResult{ID: call.ID, Name: call.Name, Output: []byte("ok")}
		}),
	}
	sid := "sess-resume-approve"
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	done := make(chan struct{})
	var out string
	var turnErr error
	go func() {
		defer close(done)
		out, turnErr = CollectTokens(ctx, r, TurnRequest{SessionID: sid, Prompt: "run echo"})
	}()

	var item *pendingApproval
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		pending := q.ListPending(sid)
		if len(pending) > 0 {
			item = pending[0]
			break
		}
		select {
		case <-done:
			t.Fatalf("turn finished before approval appeared: err=%v out=%q", turnErr, out)
		case <-time.After(5 * time.Millisecond):
		}
	}
	if item == nil {
		t.Fatal("timed out waiting for pending approval")
	}
	if item.ToolName != "shell.exec" {
		t.Fatalf("tool=%q", item.ToolName)
	}
	waiterDeadline := time.Now().Add(2 * time.Second)
	for !q.HasWaiter(item.ID) && time.Now().Before(waiterDeadline) {
		select {
		case <-done:
			t.Fatalf("turn finished before waiter registered: err=%v out=%q", turnErr, out)
		case <-time.After(5 * time.Millisecond):
		}
	}
	if !q.HasWaiter(item.ID) {
		t.Fatal("timed out waiting for approval waiter")
	}

	resolved, resumed := q.resolve(item.ID, true, "session")
	if resolved == nil || resolved.Status != "approved" || !resumed {
		t.Fatalf("resolve=%#v resumed=%v", resolved, resumed)
	}

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("turn did not resume after approve")
	}
	if turnErr != nil {
		t.Fatalf("turn err: %v", turnErr)
	}
	if executed.Load() != 1 {
		t.Fatalf("executed=%d want 1", executed.Load())
	}
	if len(q.ListPending(sid)) != 0 {
		t.Fatalf("second approval banner: %v", q.ListPending(sid))
	}
	if !strings.Contains(out, "Waiting for your approval") {
		t.Fatalf("missing wait status: %q", out)
	}
	if !strings.Contains(out, "finished") {
		t.Fatalf("missing continued model text: %q", out)
	}
	if !strings.Contains(out, "@@tool_result:") {
		t.Fatalf("missing tool result after resume: %q", out)
	}
}

func TestDenyDoesNotRunPendingToolBatch(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	q := newApprovalQueue()
	_ = q.SetMode("ask")
	input := `{"argv":["C:\\Windows\\System32\\cmd.exe","/c","echo"]}`
	var executed atomic.Int32
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "1", Name: "shell.exec", Input: []byte(input)}}},
		{{Text: "should-not-run", Done: true}},
	}}
	r := &CognitionTurnRunner{
		Model:     model,
		Registry:  reg,
		Approvals: q,
		Policy:    &RegistryPolicy{Registry: reg, Approvals: q, SessionID: "sess-resume-deny"},
		Tools: cognitionToolFunc(func(_ context.Context, call cognition.ToolCall) cognition.ToolResult {
			executed.Add(1)
			return cognition.ToolResult{ID: call.ID, Name: call.Name, Output: []byte("ok")}
		}),
	}
	sid := "sess-resume-deny"
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
	defer cancel()

	done := make(chan struct{})
	var out string
	var turnErr error
	go func() {
		defer close(done)
		out, turnErr = CollectTokens(ctx, r, TurnRequest{SessionID: sid, Prompt: "run echo"})
	}()

	var item *pendingApproval
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		pending := q.ListPending(sid)
		if len(pending) > 0 {
			item = pending[0]
			break
		}
		select {
		case <-done:
			t.Fatalf("turn finished before approval appeared: err=%v out=%q", turnErr, out)
		case <-time.After(5 * time.Millisecond):
		}
	}
	if item == nil {
		t.Fatal("timed out waiting for pending approval")
	}
	_ = q.Resolve(item.ID, false, "session")

	select {
	case <-done:
	case <-time.After(2 * time.Second):
		t.Fatal("turn did not stop after deny")
	}
	if turnErr != nil {
		t.Fatalf("turn err: %v", turnErr)
	}
	if executed.Load() != 0 {
		t.Fatalf("tool ran after deny: executed=%d", executed.Load())
	}
	if !strings.Contains(out, "Denied — stopped") {
		t.Fatalf("missing deny status: %q", out)
	}
	if strings.Contains(out, "should-not-run") {
		t.Fatalf("model continued after deny: %q", out)
	}
}

type cognitionToolFunc func(context.Context, cognition.ToolCall) cognition.ToolResult

func (f cognitionToolFunc) Execute(ctx context.Context, call cognition.ToolCall) cognition.ToolResult {
	return f(ctx, call)
}

type cognitionModelFunc func(context.Context, cognition.Turn) (<-chan cognition.ModelEvent, error)

func (f cognitionModelFunc) Stream(ctx context.Context, turn cognition.Turn) (<-chan cognition.ModelEvent, error) {
	return f(ctx, turn)
}

func (cognitionModelFunc) ContextWindow() int { return 0 }

type cognitionPolicyFunc func(context.Context, cognition.ToolCall) cognition.Decision

func (f cognitionPolicyFunc) Decide(ctx context.Context, call cognition.ToolCall) cognition.Decision {
	return f(ctx, call)
}

func TestFormatToolCallTokenFamily(t *testing.T) {
	tok := formatToolCallToken(cognition.ToolCall{Name: "x", Input: []byte(`not-json`)})
	if !strings.HasPrefix(tok, "@@tool_call:") || !strings.Contains(tok, `"_raw"`) {
		t.Fatalf("raw wrap: %q", tok)
	}
	tok2 := formatToolCallToken(cognition.ToolCall{Name: "y", Input: []byte(`{"a":1}`)})
	if !strings.Contains(tok2, `"a":1`) {
		t.Fatalf("json args: %q", tok2)
	}
}

func TestSeedTranscriptCarriesHistoryThenTheUserMessage(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{Text: "sure", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	_, err := CollectTokens(context.Background(), r, TurnRequest{
		Prompt: "and now the second one",
		History: []map[string]any{
			{"role": "user", "content": "first question"},
			{"role": "assistant", "content": "first answer"},
			{"role": "system", "content": "ignored"},
			{"role": "user", "content": ""},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	msgs := model.Turns[0].Messages
	if len(msgs) != 3 {
		t.Fatalf("round 1 transcript = %#v", msgs)
	}
	if msgs[0].Role != cognition.RoleUser || msgs[0].Text() != "first question" {
		t.Fatalf("history[0]=%#v", msgs[0])
	}
	if msgs[1].Role != cognition.RoleAssistant || msgs[1].Text() != "first answer" {
		t.Fatalf("history[1]=%#v", msgs[1])
	}
	if msgs[2].Role != cognition.RoleUser || msgs[2].Text() != "and now the second one" {
		t.Fatalf("current message=%#v", msgs[2])
	}
}

func TestSeedTranscriptReadsAttachments(t *testing.T) {
	home := t.TempDir()
	sid := "sess-att"
	dir := sessionAttachmentsDir(sid, home)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	png := filepath.Join(dir, "shot.png")
	// 1x1 PNG header bytes are enough: the runner never decodes the image.
	if err := os.WriteFile(png, []byte{0x89, 'P', 'N', 'G', 0x0d, 0x0a}, 0o600); err != nil {
		t.Fatal(err)
	}
	notes := filepath.Join(dir, "notes.md")
	if err := os.WriteFile(notes, []byte("remember the milk"), 0o600); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(t.TempDir(), "secret.txt")
	if err := os.WriteFile(outside, []byte("do not read me"), 0o600); err != nil {
		t.Fatal(err)
	}

	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{{{Text: "seen", Done: true}}}}
	r := NewCognitionTurnRunner(model)
	r.HomeDir = home
	r.forcePrimary = model // HomeDir set: keep the scripted model, do not resolve a live one
	_, err := CollectTokens(context.Background(), r, TurnRequest{
		SessionID: sid,
		Prompt:    "what is this?",
		Attachments: []map[string]any{
			{"name": "shot.png", "path": png, "mime": "image/png"},
			{"name": "notes.md", "path": notes, "mime": "text/markdown"},
			{"name": "secret.txt", "path": outside, "mime": "text/plain"},
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	blocks := model.LastTurn.Messages[0].Blocks
	var images, texts int
	var body strings.Builder
	for _, b := range blocks {
		switch b.Type {
		case cognition.BlockImage:
			images++
			if b.MediaType != "image/png" || len(b.Data) == 0 {
				t.Fatalf("image block=%#v", b)
			}
		case cognition.BlockText:
			texts++
			body.WriteString(b.Text)
		}
	}
	if images != 1 {
		t.Fatalf("want one image block, got %d: %#v", images, blocks)
	}
	if !strings.Contains(body.String(), "what is this?") {
		t.Fatalf("user text lost: %q", body.String())
	}
	if !strings.Contains(body.String(), "remember the milk") {
		t.Fatalf("text attachment not inlined: %q", body.String())
	}
	if strings.Contains(body.String(), "do not read me") || strings.Contains(body.String(), "secret.txt") {
		t.Fatalf("attachment jail leaked: %q", body.String())
	}
}

func TestModelToolSurfaceHidesDemoTools(t *testing.T) {
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	advertised := map[string]bool{}
	for _, id := range r.ModelVisibleToolIDs() {
		advertised[id] = true
	}
	// Diagnostics, and the internal ABI the frontier surface replaced.
	for _, hidden := range []string{
		"text.slugify", "text.word_count", "runtime.probe", "json.canonical", "text.sha256",
		"workspace.read", "workspace.list", "workspace.write", "workspace.edit", "workspace.search",
		"shell.exec",
	} {
		if advertised[hidden] {
			t.Fatalf("%s must not be advertised to the model", hidden)
		}
	}
	// The Go-native diagnostics and the replaced ABI stay registered for
	// probes, the CLI and approval fingerprints (text.slugify /
	// text.word_count arrive with the Python worker).
	for _, hidden := range []string{"runtime.probe", "json.canonical", "text.sha256", "shell.exec"} {
		if _, err := r.Registry.Latest(hidden); err != nil {
			t.Fatalf("%s must stay registered: %v", hidden, err)
		}
	}
	// The surface a frontier model arrives knowing.
	for _, want := range []string{
		"read", "edit", "write", "glob", "grep", "bash", "jobs", "todo",
		"screenshot", "delegate", "computer.screenshot",
	} {
		if !advertised[want] {
			t.Fatalf("%s must be on the model surface", want)
		}
	}
	for id := range advertised {
		if strings.HasPrefix(id, "prompt.") || strings.HasPrefix(id, "voice.") || strings.HasPrefix(id, "vision.") {
			t.Fatalf("internal tool advertised: %s", id)
		}
	}
}
