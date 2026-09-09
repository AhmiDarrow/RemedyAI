package httpapi

import (
	"bytes"
	"context"
	"encoding/base64"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/ipc"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// fakeOpenAI is an httptest chat-completions server. rounds decides what the
// n-th request for a given user prompt streams back.
type fakeOpenAI struct {
	mu       sync.Mutex
	counts   map[string]int
	requests []map[string]any
	rounds   func(prompt string, n int) string
}

func (f *fakeOpenAI) handler(t *testing.T) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		defer r.Body.Close()
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Errorf("decode: %v", err)
			return
		}
		prompt := ""
		msgs, _ := body["messages"].([]any)
		for _, m := range msgs {
			mm, _ := m.(map[string]any)
			if mm["role"] == "user" {
				prompt, _ = mm["content"].(string)
			}
		}
		f.mu.Lock()
		if f.counts == nil {
			f.counts = map[string]int{}
		}
		f.counts[prompt]++
		n := f.counts[prompt]
		f.requests = append(f.requests, body)
		f.mu.Unlock()
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = io.WriteString(w, f.rounds(prompt, n))
	}
}

func (f *fakeOpenAI) snapshot() []map[string]any {
	f.mu.Lock()
	defer f.mu.Unlock()
	return append([]map[string]any(nil), f.requests...)
}

func sseToolCallRound(id, name, args string) string {
	argsJSON, _ := json.Marshal(args)
	return `data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"` + id + `","function":{"name":"` + name + `","arguments":` + string(argsJSON) + `}}]},"finish_reason":"tool_calls"}]}` + "\n\n" +
		"data: [DONE]\n\n"
}

func sseTextRound(text string) string {
	textJSON, _ := json.Marshal(text)
	return `data: {"choices":[{"delta":{"content":` + string(textJSON) + `},"finish_reason":"stop"}]}` + "\n\n" +
		"data: [DONE]\n\n"
}

func waitPending(t *testing.T, q *approvalQueue, sid string, done <-chan struct{}) *pendingApproval {
	t.Helper()
	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if pending := q.ListPending(sid); len(pending) > 0 {
			item := pending[0]
			for !q.HasWaiter(item.ID) && time.Now().Before(deadline) {
				time.Sleep(5 * time.Millisecond)
			}
			return item
		}
		select {
		case <-done:
			t.Fatalf("turn finished before an approval appeared")
		case <-time.After(5 * time.Millisecond):
		}
	}
	t.Fatal("timed out waiting for pending approval")
	return nil
}

func TestApprovalFingerprintUsesABIIdOverSSE(t *testing.T) {
	fake := &fakeOpenAI{rounds: func(_ string, n int) string {
		if n == 1 {
			return sseToolCallRound("call_1", "computer_click", `{"x":10,"y":10}`)
		}
		return sseTextRound("finished")
	}}
	srv := httptest.NewServer(fake.handler(t))
	defer srv.Close()

	oc := &providers.OpenAICompat{BaseURL: srv.URL + "/v1", APIKey: "unused", Model: "x", HTTPClient: srv.Client()}
	q := newApprovalQueue()
	_ = q.SetMode("ask")
	var executed atomic.Int32
	var executedName string
	r := NewCognitionTurnRunner(oc)
	r.Approvals = q
	r.Tools = cognitionToolFunc(func(_ context.Context, call cognition.ToolCall) cognition.ToolResult {
		executed.Add(1)
		executedName = call.Name
		return cognition.ToolResult{ID: call.ID, Name: call.Name, Output: []byte("ok")}
	})
	// The advertised name differs from the ABI id — that gap is where the
	// fingerprint bug lived, so the test needs a tool whose name is rewritten.
	// The fake server is loopback, which otherwise narrows the surface to the
	// coding pack, whose Go-native ids need no rewriting at all.
	oc.SetTools(r.registryToolSurface(false))
	if oc.ToolNameMap["computer_click"] != "computer.click" {
		t.Fatalf("advertised map missing computer_click: %v", oc.ToolNameMap)
	}

	sid := "sess-abi-approve"
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	done := make(chan struct{})
	var out string
	var turnErr error
	go func() {
		defer close(done)
		out, turnErr = CollectTokens(ctx, r, TurnRequest{SessionID: sid, Prompt: "click it"})
	}()

	item := waitPending(t, q, sid, done)
	if item.ToolName != "computer.click" {
		t.Fatalf("enqueued name=%q want ABI id computer.click", item.ToolName)
	}
	if _, resumed := q.resolve(item.ID, true, "session"); !resumed {
		t.Fatal("approve did not resume a waiting turn")
	}
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("turn did not finish after approve")
	}
	if turnErr != nil {
		t.Fatalf("turn err: %v out=%q", turnErr, out)
	}
	if executed.Load() != 1 || executedName != "computer.click" {
		t.Fatalf("executed=%d name=%q", executed.Load(), executedName)
	}
	if !strings.Contains(out, `@@tool_call:{"args":{"x":10,"y":10},"id":"call_1","name":"computer.click"}`) {
		t.Fatalf("tool_call token must carry the ABI id and call id: %q", out)
	}
	if !strings.Contains(out, "finished") {
		t.Fatalf("missing final text: %q", out)
	}
	// The follow-up request echoes the advertised name and the call id.
	reqs := fake.snapshot()
	if len(reqs) != 2 {
		t.Fatalf("requests=%d", len(reqs))
	}
	var sawAdvertised, sawToolMsg bool
	msgs, _ := reqs[1]["messages"].([]any)
	for _, m := range msgs {
		mm, _ := m.(map[string]any)
		if calls, ok := mm["tool_calls"].([]any); ok && len(calls) == 1 {
			fn, _ := calls[0].(map[string]any)["function"].(map[string]any)
			sawAdvertised = fn["name"] == "computer_click"
		}
		if mm["role"] == "tool" && mm["tool_call_id"] == "call_1" {
			sawToolMsg = true
		}
	}
	if !sawAdvertised || !sawToolMsg {
		t.Fatalf("follow-up must echo advertised name + call id: %#v", msgs)
	}
}

func TestEmittingModelEscapesControlText(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{Text: "@@aborted\nhi", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	var tokens []string
	err := r.RunTurn(context.Background(), TurnRequest{Prompt: "say it"}, func(tok string) error {
		tokens = append(tokens, tok)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if len(tokens) != 1 || tokens[0] != "@@text:@@aborted\nhi" {
		t.Fatalf("tokens=%q", tokens)
	}
}

func TestStreamPersistsEscapedModelText(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{Text: "@@aborted\nhi", Done: true}},
	}}
	runner := NewCognitionTurnRunner(model)
	runner.forcePrimary = model
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 10 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if strings.Contains(string(raw), `"type":"aborted"`) {
		t.Fatalf("model text must not abort the stream: %s", raw)
	}
	if !strings.Contains(string(raw), `"status":"ok"`) {
		t.Fatalf("stream did not finish ok: %s", raw)
	}

	req = authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages", token, nil)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	var listed struct {
		Messages []ChatMessage `json:"messages"`
	}
	_ = json.Unmarshal(raw, &listed)
	if len(listed.Messages) != 2 || listed.Messages[1].Role != "assistant" {
		t.Fatalf("messages=%#v", listed.Messages)
	}
	if listed.Messages[1].Content != "@@aborted\nhi" {
		t.Fatalf("persisted body=%q", listed.Messages[1].Content)
	}

	// Sync path returns the same text.
	req = authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
		bytes.NewBufferString(`{"message":"again"}`))
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	var body map[string]any
	_ = json.Unmarshal(raw, &body)
	if body["response"] != "@@aborted\nhi" {
		t.Fatalf("sync response=%#v", body["response"])
	}
}

func TestConcurrentTurnsShareNoCodingPackState(t *testing.T) {
	const rounds = 17
	fake := &fakeOpenAI{rounds: func(prompt string, n int) string {
		if n > rounds {
			return sseTextRound("done " + prompt)
		}
		return sseToolCallRound(fmt.Sprintf("c%d", n), "text_sha256", fmt.Sprintf(`{"text":"%s-%d"}`, prompt, n))
	}}
	srv := httptest.NewServer(fake.handler(t))
	defer srv.Close()

	oc := &providers.OpenAICompat{BaseURL: srv.URL + "/v1", APIKey: "unused", Model: "x", HTTPClient: srv.Client()}
	r := NewCognitionTurnRunner(oc)
	r.Config = cognition.Config{SoftEpochSteps: 16, MaxRepeatedBatch: 64}

	var wg sync.WaitGroup
	outs := make([]string, 2)
	errs := make([]error, 2)
	for i, prompt := range []string{"alpha", "beta"} {
		wg.Add(1)
		go func(i int, prompt string) {
			defer wg.Done()
			outs[i], errs[i] = CollectTokens(context.Background(), r, TurnRequest{SessionID: "s" + prompt, Prompt: prompt})
		}(i, prompt)
	}
	wg.Wait()
	for i, prompt := range []string{"alpha", "beta"} {
		if errs[i] != nil {
			t.Fatalf("%s: %v", prompt, errs[i])
		}
		if !strings.Contains(outs[i], "done "+prompt) {
			t.Fatalf("%s: missing completion in %q", prompt, outs[i])
		}
		if !strings.Contains(outs[i], "Switched to coding tool pack") {
			t.Fatalf("%s: expected epoch coding switch in %q", prompt, outs[i])
		}
	}
	if len(oc.Tools) == 0 {
		t.Fatal("runner model schemas must stay intact after per-turn copies")
	}
}

func TestProviderUnusableAfterEmitDoesNotFallBack(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	var rounds atomic.Int32
	failing := cognitionModelFunc(func(_ context.Context, turn cognition.Turn) (<-chan cognition.ModelEvent, error) {
		if rounds.Add(1) == 1 {
			ch := make(chan cognition.ModelEvent, 1)
			ch <- cognition.ModelEvent{ToolCall: &cognition.ToolCall{ID: "1", Name: "runtime.probe", Input: []byte(`{}`)}}
			close(ch)
			return ch, nil
		}
		return nil, errors.New("openai-compat HTTP 402: subscription required")
	})
	prov := "poe"
	r := NewCognitionTurnRunner(nil)
	r.HomeDir = home
	r.forcePrimary = failing
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "probe", Provider: &prov})
	if err == nil || !strings.Contains(err.Error(), "402") {
		t.Fatalf("want the provider error surfaced, got err=%v out=%q", err, out)
	}
	if strings.Contains(out, "switching to your usual model") || strings.Contains(out, "Hello world") {
		t.Fatalf("must not fall back after tokens were emitted: %q", out)
	}
	if !strings.Contains(out, "send continue") {
		t.Fatalf("missing continue hint: %q", out)
	}
}

func TestOwnerCheckpointEmitsPendingCallOnce(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "pay", Name: "payment.submit", Input: []byte(`{}`)}}},
	}}
	r := &CognitionTurnRunner{
		Model:  model,
		Policy: cognitionPolicyFunc(func(context.Context, cognition.ToolCall) cognition.Decision { return cognition.Ask }),
		Tools:  cognition.EchoTools{},
	}
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "pay"})
	if err != nil {
		t.Fatal(err)
	}
	if n := strings.Count(out, "@@tool_call:"); n != 1 {
		t.Fatalf("pending call emitted %d times: %q", n, out)
	}
}

func TestParseToolTokensCarryIDs(t *testing.T) {
	call := parseToolCallToken(formatToolCallToken(cognition.ToolCall{ID: "c7", Name: "shell.exec", Input: []byte(`{"argv":["x"]}`)}))
	if call["id"] != "c7" || call["name"] != "shell.exec" {
		t.Fatalf("call=%#v", call)
	}
	res := parseToolResultToken(formatToolResultToken(cognition.ToolResult{ID: "c7", Name: "shell.exec", Err: "boom"}))
	if res.ID != "c7" || res.Name != "shell.exec" || res.OK || res.Preview != "boom" {
		t.Fatalf("res=%#v", res)
	}
	rec := res.record()
	if rec["id"] != "c7" || rec["error"] != "boom" {
		t.Fatalf("record=%#v", rec)
	}
	if _, has := parseToolCallToken("@@tool_call:{\"name\":\"x\",\"args\":{}}")["id"]; has {
		t.Fatal("no id must not add an id key")
	}
}

func TestMessengerOriginForcesAskUnderAuto(t *testing.T) {
	reg, err := NewDefaultToolRegistry(nil)
	if err != nil {
		t.Fatal(err)
	}
	var executed atomic.Int32
	err = reg.Register(tools.Descriptor{
		ID: "workspace.write", Version: 1, Description: "test write", Risk: tools.RiskMutation,
		Runtime: tools.RuntimeGo, InputSchema: json.RawMessage(`{"type":"object"}`), OutputSchema: json.RawMessage(`{"type":"object"}`),
	}, tools.ExecutorFunc(func(context.Context, tools.Request) (tools.Result, error) {
		executed.Add(1)
		return tools.Result{Output: []byte(`{}`)}, nil
	}))
	if err != nil {
		t.Fatal(err)
	}
	q := newApprovalQueue()
	_ = q.SetMode("auto")
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "1", Name: "workspace.write", Input: []byte(`{"path":"a.txt","content":"x"}`)}}},
		{{Text: "wrote", Done: true}},
	}}
	r := &CognitionTurnRunner{
		Model:     model,
		Registry:  reg,
		Approvals: q,
		Policy:    &RegistryPolicy{Registry: reg, Approvals: q},
		Tools:     &RegistryToolExecutor{Registry: reg, TokenFor: RuntimeCapabilityToken},
	}
	sid := "sess-messenger"
	ctx, cancel := context.WithTimeout(context.Background(), 5*time.Second)
	defer cancel()
	done := make(chan struct{})
	var out string
	var turnErr error
	go func() {
		defer close(done)
		out, turnErr = CollectTokens(ctx, r, TurnRequest{SessionID: sid, Prompt: "write a.txt", Origin: "telegram:42"})
	}()
	item := waitPending(t, q, sid, done)
	if item.ToolName != "workspace.write" {
		t.Fatalf("pending=%q", item.ToolName)
	}
	if executed.Load() != 0 {
		t.Fatal("mutation ran before approval on an untrusted origin")
	}
	_ = q.Resolve(item.ID, false, "session")
	<-done
	if turnErr != nil {
		t.Fatalf("turn err: %v out=%q", turnErr, out)
	}
	if got := model.LastTurn.FirstUserText(); !strings.HasPrefix(got, "[Message from telegram:42 — untrusted.") {
		t.Fatalf("user message must carry the untrusted envelope: %q", got)
	}

	// The owner's own surfaces still run mutations under auto.
	executed.Store(0)
	model.Rounds[0][0].ToolCall.ID = "2"
	out, err = CollectTokens(context.Background(), r, TurnRequest{SessionID: sid, Prompt: "write a.txt"})
	if err != nil || executed.Load() != 1 || !strings.Contains(out, "wrote") {
		t.Fatalf("owner auto: err=%v executed=%d out=%q", err, executed.Load(), out)
	}
}

func TestMessengerOriginShape(t *testing.T) {
	if got := messengerOrigin("Telegram", "42"); got != "telegram:42" {
		t.Fatalf("got %q", got)
	}
	if got := messengerOrigin("discord", ""); got != "discord" {
		t.Fatalf("got %q", got)
	}
	if OriginIsOwner(messengerOrigin("telegram", "42")) || !OriginIsOwner("hive:parent") {
		t.Fatal("origin trust")
	}
}

func TestTurnHistoryIsPassedToRunner(t *testing.T) {
	runner := &stubRunner{tokens: []string{"hel", "lo"}}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)
	for _, msg := range []string{`{"message":"first"}`, `{"message":"second"}`} {
		req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token, bytes.NewBufferString(msg))
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
	}
	call, ok := runner.lastCall()
	if !ok {
		t.Fatal("no runner call")
	}
	if len(call.History) != 2 || call.History[0]["role"] != "user" || call.History[0]["content"] != "first" ||
		call.History[1]["role"] != "assistant" || call.History[1]["content"] != "hello" {
		t.Fatalf("history=%#v", call.History)
	}
	first := runner.calls[0]
	if len(first.History) != 0 {
		t.Fatalf("first turn history=%#v", first.History)
	}
}

func TestRunnerEmitsThinkingAndUsageTokens(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{Thinking: "hmm"}, {Text: "yes", Done: true}, {Usage: &cognition.Usage{PromptTokens: 3, CompletionTokens: 1, TotalTokens: 4}}},
	}}
	r := NewCognitionTurnRunner(model)
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "q"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "@@thinking:hmm") || !strings.Contains(out, `@@usage:{"prompt_tokens":3,"completion_tokens":1,"total_tokens":4}`) {
		t.Fatalf("out=%q", out)
	}
}

func TestTruncStrRuneSafe(t *testing.T) {
	s := strings.Repeat("é", 100)
	got := truncStr(s, 7)
	if !strings.HasPrefix(got, strings.Repeat("é", 3)+"\n…[truncated") {
		t.Fatalf("got %q", got)
	}
}

func TestAttachmentImageReachesTheProviderPayload(t *testing.T) {
	home := t.TempDir()
	sid := "sess-vision"
	dir := sessionAttachmentsDir(sid, home)
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	raw := []byte{0x89, 'P', 'N', 'G', 0x0d, 0x0a, 0x1a, 0x0a}
	png := filepath.Join(dir, "shot.png")
	if err := os.WriteFile(png, raw, 0o600); err != nil {
		t.Fatal(err)
	}
	fake := &fakeOpenAI{rounds: func(string, int) string { return sseTextRound("a login screen") }}
	srv := httptest.NewServer(fake.handler(t))
	defer srv.Close()

	oc := &providers.OpenAICompat{BaseURL: srv.URL + "/v1", APIKey: "unused", Model: "x", HTTPClient: srv.Client()}
	r := NewCognitionTurnRunner(oc)
	r.HomeDir = home
	r.forcePrimary = oc // HomeDir set: keep the fake endpoint, do not resolve a live one
	out, err := CollectTokens(context.Background(), r, TurnRequest{
		SessionID:   sid,
		Prompt:      "what is on this screen?",
		Attachments: []map[string]any{{"name": "shot.png", "path": png, "mime": "image/png"}},
	})
	if err != nil {
		t.Fatalf("err=%v out=%q", err, out)
	}
	reqs := fake.snapshot()
	if len(reqs) != 1 {
		t.Fatalf("requests=%d", len(reqs))
	}
	wantURL := "data:image/png;base64," + base64.StdEncoding.EncodeToString(raw)
	var sawImage bool
	msgs, _ := reqs[0]["messages"].([]any)
	for _, m := range msgs {
		mm, _ := m.(map[string]any)
		parts, ok := mm["content"].([]any)
		if !ok {
			continue
		}
		for _, part := range parts {
			pp, _ := part.(map[string]any)
			if pp["type"] != "image_url" {
				continue
			}
			img, _ := pp["image_url"].(map[string]any)
			if img["url"] == wantURL {
				sawImage = true
			}
		}
	}
	if !sawImage {
		t.Fatalf("attachment image never reached the provider: %#v", msgs)
	}
}

func TestHistoryIsNotAlsoSentToPromptAssemble(t *testing.T) {
	// The transcript carries prior turns as real messages now, so history must
	// not be rendered into the system block as well (it would appear twice).
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

	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{{{Text: "ok", Done: true}}}}
	r := NewCognitionTurnRunner(model)
	if err := r.AttachPythonWorker(client); err != nil {
		t.Fatal(err)
	}
	history := []map[string]any{{"role": "user", "content": "the earlier question"}}
	if _, err := CollectTokens(context.Background(), r, TurnRequest{
		Prompt: "the new one", SessionID: "s-hist", History: history,
	}); err != nil {
		t.Fatal(err)
	}
	if strings.Contains(model.LastTurn.System, "the earlier question") {
		t.Fatalf("history must not be rendered into the system block: %q", model.LastTurn.System)
	}
	if model.LastTurn.Messages[0].Text() != "the earlier question" {
		t.Fatalf("history must reach round 1 as a message: %#v", model.LastTurn.Messages)
	}
}

func TestCodingPackSwitchKeepsTheCloudContextWindow(t *testing.T) {
	// Mid-build the runner narrows the advertised schemas. That must not turn a
	// cloud model into a 16k local one: the transcript is the model's memory.
	r := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	cloud := &providers.OpenAICompat{BaseURL: "https://api.example.com/v1", Model: "x"}
	r.advertiseToolSurface(cloud, false)
	full := len(cloud.Tools)
	r.advertiseToolSurface(cloud, true)
	if len(cloud.Tools) >= full {
		t.Fatalf("coding pack did not narrow the surface: %d → %d", full, len(cloud.Tools))
	}
	if cloud.LocalFit || cloud.NCtx != 0 {
		t.Fatalf("cloud model was fitted to a local window: LocalFit=%v NCtx=%d", cloud.LocalFit, cloud.NCtx)
	}
	if got := cloud.ContextWindow(); got != cognition.DefaultContextWindow {
		t.Fatalf("cloud window=%d", got)
	}
	local := &providers.OpenAICompat{BaseURL: "http://127.0.0.1:8741/v1", Model: "x"}
	r.advertiseToolSurface(local, false)
	if !local.LocalFit || local.ContextWindow() != providers.LocalContextWindow {
		t.Fatalf("loopback model must keep the local fitter: %#v", local)
	}
}
