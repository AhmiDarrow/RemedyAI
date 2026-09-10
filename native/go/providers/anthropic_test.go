package providers

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

// sseEvent renders one Anthropic SSE frame.
func sseEvent(kind, data string) string {
	return "event: " + kind + "\ndata: " + data + "\n\n"
}

// roundSSE is a complete model round: one text block, then stop_reason+usage.
func roundSSE(text, stopReason string) string {
	return sseEvent("message_start", `{"type":"message_start","message":{"id":"msg_1","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"usage":{"input_tokens":11,"output_tokens":1,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}}`) +
		sseEvent("content_block_start", `{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`) +
		sseEvent("content_block_delta", `{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":`+jsonQuote(text)+`}}`) +
		sseEvent("content_block_stop", `{"type":"content_block_stop","index":0}`) +
		sseEvent("message_delta", `{"type":"message_delta","delta":{"stop_reason":"`+stopReason+`","stop_sequence":null},"usage":{"input_tokens":11,"output_tokens":7,"cache_read_input_tokens":0,"cache_creation_input_tokens":0}}`) +
		sseEvent("message_stop", `{"type":"message_stop"}`)
}

func jsonQuote(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

// recorder is an httptest server that replays canned SSE bodies in order and
// keeps every decoded request payload. No test ever reaches the real API.
type recorder struct {
	mu       sync.Mutex
	bodies   []map[string]any
	replies  []string
	statuses []int
}

func newRecorder(replies ...string) *recorder { return &recorder{replies: replies} }

func (r *recorder) serve(t *testing.T) *httptest.Server {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, req *http.Request) {
		raw, _ := io.ReadAll(req.Body)
		var decoded map[string]any
		_ = json.Unmarshal(raw, &decoded)
		r.mu.Lock()
		idx := len(r.bodies)
		r.bodies = append(r.bodies, decoded)
		status := http.StatusOK
		if idx < len(r.statuses) {
			status = r.statuses[idx]
		}
		body := ""
		if idx < len(r.replies) {
			body = r.replies[idx]
		}
		r.mu.Unlock()
		if status != http.StatusOK {
			w.Header().Set("content-type", "application/json")
			w.WriteHeader(status)
			_, _ = io.WriteString(w, `{"type":"error","error":{"type":"authentication_error","message":"invalid x-api-key"}}`)
			return
		}
		w.Header().Set("content-type", "text/event-stream")
		w.WriteHeader(http.StatusOK)
		_, _ = io.WriteString(w, body)
	}))
	t.Cleanup(srv.Close)
	return srv
}

func (r *recorder) calls() int {
	r.mu.Lock()
	defer r.mu.Unlock()
	return len(r.bodies)
}

func (r *recorder) body(i int) map[string]any {
	r.mu.Lock()
	defer r.mu.Unlock()
	if i >= len(r.bodies) {
		return nil
	}
	return r.bodies[i]
}

// collect drains a model stream into a slice.
func collect(t *testing.T, a *Anthropic, turn cognition.Turn) []cognition.ModelEvent {
	t.Helper()
	events, err := a.Stream(context.Background(), turn)
	if err != nil {
		t.Fatalf("Stream: %v", err)
	}
	var out []cognition.ModelEvent
	for ev := range events {
		out = append(out, ev)
	}
	return out
}

func newAdapter(srv *httptest.Server, model string) *Anthropic {
	return &Anthropic{
		BaseURL:    srv.URL,
		APIKey:     "sk-ant-test",
		Model:      model,
		HTTPClient: srv.Client(),
	}
}

func simpleTurn() cognition.Turn {
	return cognition.Turn{
		System:   "OPERATIONAL PROMPT\n\nRecent conversation (oldest first; the current message follows separately — do not repeat it):\nuser: hi",
		Messages: []cognition.Message{cognition.UserText("build the thing")},
	}
}

func registrySurface() []RegistryTool {
	return []RegistryTool{
		{
			ID:          "workspace.read",
			Description: "Read a file",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"path":{"type":"string"}},"required":["path"],"additionalProperties":false}`),
		},
		{
			ID:          "shell.exec",
			Description: "Run a command",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"argv":{"type":"array"}},"required":["argv"],"additionalProperties":false}`),
		},
		{
			ID:          "web.search",
			Description: "Search the web",
			InputSchema: json.RawMessage(`{"type":"object","properties":{"q":{"type":"string"}}}`),
		},
	}
}

func TestAnthropicCacheBreakpointsLandInRequest(t *testing.T) {
	rec := newRecorder(roundSSE("done", "end_turn"))
	srv := rec.serve(t)
	a := newAdapter(srv, "claude-opus-5")
	a.SetTools(registrySurface())
	a.Shape = ShapeBuild

	collect(t, a, simpleTurn())

	body := rec.body(0)
	if body == nil {
		t.Fatalf("no request captured")
	}

	system, _ := body["system"].([]any)
	if len(system) != 2 {
		t.Fatalf("want a stable + volatile system, got %d blocks: %v", len(system), body["system"])
	}
	stable, _ := system[0].(map[string]any)
	if !strings.HasPrefix(stable["text"].(string), "OPERATIONAL PROMPT") {
		t.Fatalf("stable block is not the operational prompt: %v", stable["text"])
	}
	cache, _ := stable["cache_control"].(map[string]any)
	if cache == nil || cache["type"] != "ephemeral" {
		t.Fatalf("stable system block has no ephemeral breakpoint: %v", stable)
	}
	if cache["ttl"] != "1h" {
		t.Fatalf("a build should cache the operational prompt for 1h, got %v", cache["ttl"])
	}
	volatile, _ := system[1].(map[string]any)
	if _, ok := volatile["cache_control"]; ok {
		t.Fatalf("the volatile tail must not carry a breakpoint: %v", volatile)
	}
	if !strings.HasPrefix(volatile["text"].(string), "Recent conversation") {
		t.Fatalf("volatile tail is not the history block: %v", volatile["text"])
	}

	tools, _ := body["tools"].([]any)
	if len(tools) != 3 {
		t.Fatalf("want 3 tools, got %d", len(tools))
	}
	last, _ := tools[len(tools)-1].(map[string]any)
	if _, ok := last["cache_control"]; !ok {
		t.Fatalf("the last tool must carry the schema breakpoint: %v", last)
	}
	for _, tool := range tools[:len(tools)-1] {
		if _, ok := tool.(map[string]any)["cache_control"]; ok {
			t.Fatalf("only the last tool carries a breakpoint: %v", tool)
		}
	}

	messages, _ := body["messages"].([]any)
	if len(messages) != 1 {
		t.Fatalf("want 1 message, got %d", len(messages))
	}
	content, _ := messages[0].(map[string]any)["content"].([]any)
	lastBlock, _ := content[len(content)-1].(map[string]any)
	if _, ok := lastBlock["cache_control"]; !ok {
		t.Fatalf("the last user message must carry a breakpoint: %v", lastBlock)
	}
}

func TestAnthropicChatShapeUsesShortCacheTTL(t *testing.T) {
	rec := newRecorder(roundSSE("hi", "end_turn"))
	srv := rec.serve(t)
	a := newAdapter(srv, "claude-opus-5") // no tools advertised → chat shape

	collect(t, a, simpleTurn())

	system, _ := rec.body(0)["system"].([]any)
	cache, _ := system[0].(map[string]any)["cache_control"].(map[string]any)
	if cache == nil {
		t.Fatalf("chat still caches the operational prompt")
	}
	if _, ok := cache["ttl"]; ok {
		t.Fatalf("chat should use the default 5m TTL, got %v", cache["ttl"])
	}
	if effort := effortJSON(t, rec.body(0)); effort != "medium" {
		t.Fatalf("chat effort = %q, want medium", effort)
	}
}

func effortJSON(t *testing.T, body map[string]any) string {
	t.Helper()
	cfg, _ := body["output_config"].(map[string]any)
	if cfg == nil {
		return ""
	}
	s, _ := cfg["effort"].(string)
	return s
}

func TestAnthropicEffortByShape(t *testing.T) {
	for _, tc := range []struct {
		shape TurnShape
		model string
		want  string
	}{
		{ShapeChat, "claude-opus-5", "medium"},
		{ShapePlan, "claude-opus-5", "high"},
		{ShapeBuild, "claude-opus-5", "xhigh"},
		{ShapeMission, "claude-opus-5", "xhigh"},
		// The 4.6 generation has no xhigh level.
		{ShapeBuild, "claude-sonnet-4-6", "high"},
	} {
		rec := newRecorder(roundSSE("ok", "end_turn"))
		srv := rec.serve(t)
		a := newAdapter(srv, tc.model)
		a.Shape = tc.shape
		collect(t, a, simpleTurn())
		if got := effortJSON(t, rec.body(0)); got != tc.want {
			t.Fatalf("%s/%s effort = %q, want %q", tc.model, tc.shape, got, tc.want)
		}
	}
}

func TestAnthropicHaikuOmitsAdaptiveThinkingAndEffort(t *testing.T) {
	rec := newRecorder(roundSSE("ok", "end_turn"))
	srv := rec.serve(t)
	a := newAdapter(srv, "claude-haiku-4-5")
	collect(t, a, simpleTurn())

	body := rec.body(0)
	if _, ok := body["thinking"]; ok {
		t.Fatalf("Haiku 4.5 rejects adaptive thinking: %v", body["thinking"])
	}
	if _, ok := body["output_config"]; ok {
		t.Fatalf("Haiku 4.5 rejects effort: %v", body["output_config"])
	}
	if a.ContextWindow() != anthropicHaikuWindow {
		t.Fatalf("Haiku window = %d", a.ContextWindow())
	}
}

func TestAnthropicAdaptiveThinkingRequestsSummaries(t *testing.T) {
	rec := newRecorder(
		sseEvent("message_start", `{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"usage":{"input_tokens":5,"output_tokens":1}}}`) +
			sseEvent("content_block_start", `{"type":"content_block_start","index":0,"content_block":{"type":"thinking","thinking":"","signature":""}}`) +
			sseEvent("content_block_delta", `{"type":"content_block_delta","index":0,"delta":{"type":"thinking_delta","thinking":"weighing options"}}`) +
			sseEvent("content_block_stop", `{"type":"content_block_stop","index":0}`) +
			sseEvent("message_delta", `{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"output_tokens":3}}`) +
			sseEvent("message_stop", `{"type":"message_stop"}`),
	)
	srv := rec.serve(t)
	a := newAdapter(srv, "claude-opus-5")

	events := collect(t, a, simpleTurn())

	thinking, _ := rec.body(0)["thinking"].(map[string]any)
	if thinking["type"] != "adaptive" || thinking["display"] != "summarized" {
		t.Fatalf("thinking = %v, want adaptive/summarized", thinking)
	}
	if _, ok := thinking["budget_tokens"]; ok {
		t.Fatalf("budget_tokens is a 400 on this model line: %v", thinking)
	}
	var summary strings.Builder
	for _, ev := range events {
		summary.WriteString(ev.Thinking)
	}
	if summary.String() != "weighing options" {
		t.Fatalf("thinking summary = %q", summary.String())
	}
}

func TestAnthropicFableNeverSendsForcedToolChoiceOrBudget(t *testing.T) {
	rec := newRecorder(roundSSE("ok", "end_turn"))
	srv := rec.serve(t)
	a := newAdapter(srv, "claude-fable-5-1")
	a.SetTools(registrySurface())
	a.Shape = ShapeBuild

	collect(t, a, simpleTurn())

	body := rec.body(0)
	if _, ok := body["tool_choice"]; ok {
		// any/tool return a 400 on Fable 5.1; auto is the default.
		t.Fatalf("tool_choice must never be sent: %v", body["tool_choice"])
	}
	raw, _ := json.Marshal(body)
	if strings.Contains(string(raw), "budget_tokens") {
		t.Fatalf("budget_tokens must never be sent: %s", raw)
	}
	if effortJSON(t, body) != "xhigh" {
		t.Fatalf("Fable build effort = %q", effortJSON(t, body))
	}
}

func TestAnthropicStrictToolsOnlyForClosedSchemas(t *testing.T) {
	rec := newRecorder(roundSSE("ok", "end_turn"))
	srv := rec.serve(t)
	a := newAdapter(srv, "claude-opus-5")
	a.SetTools(registrySurface())

	collect(t, a, simpleTurn())

	tools, _ := rec.body(0)["tools"].([]any)
	byName := map[string]map[string]any{}
	for _, tool := range tools {
		m, _ := tool.(map[string]any)
		byName[m["name"].(string)] = m
	}
	// Dotted ABI ids cannot go on the wire: Anthropic tool names are
	// ^[a-zA-Z0-9_-]+$.
	read, ok := byName["workspace_read"]
	if !ok {
		t.Fatalf("tools advertised under %v", keysOf(byName))
	}
	if read["strict"] != true {
		t.Fatalf("closed schema should be strict: %v", read)
	}
	schema, _ := read["input_schema"].(map[string]any)
	if schema["additionalProperties"] != false {
		t.Fatalf("additionalProperties lost in translation: %v", schema)
	}
	if req, _ := schema["required"].([]any); len(req) != 1 || req[0] != "path" {
		t.Fatalf("required lost in translation: %v", schema)
	}
	if open := byName["web_search"]; open["strict"] == true {
		t.Fatalf("an open schema must not be strict: %v", open)
	}
}

func keysOf(m map[string]map[string]any) []string {
	out := make([]string, 0, len(m))
	for k := range m {
		out = append(out, k)
	}
	return out
}

func TestAnthropicToolCallRoundTripsABIID(t *testing.T) {
	toolRound := sseEvent("message_start", `{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"usage":{"input_tokens":9,"output_tokens":1}}}`) +
		sseEvent("content_block_start", `{"type":"content_block_start","index":0,"content_block":{"type":"tool_use","id":"toolu_1","name":"workspace_read","input":{}}}`) +
		sseEvent("content_block_delta", `{"type":"content_block_delta","index":0,"delta":{"type":"input_json_delta","partial_json":"{\"path\":\"a.txt\"}"}}`) +
		sseEvent("content_block_stop", `{"type":"content_block_stop","index":0}`) +
		sseEvent("message_delta", `{"type":"message_delta","delta":{"stop_reason":"tool_use"},"usage":{"output_tokens":4}}`) +
		sseEvent("message_stop", `{"type":"message_stop"}`)

	rec := newRecorder(toolRound, roundSSE("done", "end_turn"))
	srv := rec.serve(t)
	a := newAdapter(srv, "claude-opus-5")
	a.SetTools(registrySurface())

	events := collect(t, a, simpleTurn())

	var call *cognition.ToolCall
	done := false
	for _, ev := range events {
		if ev.ToolCall != nil {
			call = ev.ToolCall
		}
		done = done || ev.Done
	}
	if call == nil {
		t.Fatalf("no tool call emitted: %+v", events)
	}
	if call.Name != "workspace.read" {
		t.Fatalf("ToolCall.Name = %q, want the Tool ABI id (approvals fingerprint it)", call.Name)
	}
	if call.Advertised != "workspace_read" {
		t.Fatalf("ToolCall.Advertised = %q", call.Advertised)
	}
	if string(call.Input) != `{"path":"a.txt"}` {
		t.Fatalf("ToolCall.Input = %s", call.Input)
	}
	if done {
		t.Fatalf("stop_reason tool_use must not end the round")
	}

	// The replayed transcript must show the tool_use under the same wire name.
	turn := simpleTurn()
	turn.Messages = append(turn.Messages,
		cognition.Message{Role: cognition.RoleAssistant, Blocks: []cognition.Block{cognition.ToolUseBlock(*call)}},
		cognition.Message{Role: cognition.RoleUser, Blocks: []cognition.Block{{
			Type:      cognition.BlockToolResult,
			ToolUseID: "toolu_1",
			Content:   []cognition.Block{cognition.TextBlock("file body")},
		}}},
	)
	collect(t, a, turn)

	messages, _ := rec.body(1)["messages"].([]any)
	if len(messages) != 3 {
		t.Fatalf("want 3 rendered messages, got %d", len(messages))
	}
	assistant, _ := messages[1].(map[string]any)
	if assistant["role"] != "assistant" {
		t.Fatalf("second message role = %v", assistant["role"])
	}
	block, _ := assistant["content"].([]any)[0].(map[string]any)
	if block["type"] != "tool_use" || block["name"] != "workspace_read" {
		t.Fatalf("replayed tool_use = %v", block)
	}
	result, _ := messages[2].(map[string]any)
	if result["role"] != "user" {
		t.Fatalf("tool results must ride in a user message, got %v", result["role"])
	}
	rblock, _ := result["content"].([]any)[0].(map[string]any)
	if rblock["type"] != "tool_result" || rblock["tool_use_id"] != "toolu_1" {
		t.Fatalf("tool_result = %v", rblock)
	}
}

func TestAnthropicImagesReachThePayload(t *testing.T) {
	rec := newRecorder(roundSSE("ok", "end_turn"))
	srv := rec.serve(t)
	a := newAdapter(srv, "claude-opus-5")

	png := []byte{0x89, 'P', 'N', 'G', 0x0d}
	turn := simpleTurn()
	turn.Messages = []cognition.Message{
		{Role: cognition.RoleUser, Blocks: []cognition.Block{
			cognition.TextBlock("look"),
			cognition.ImageBlock("image/png", png),
		}},
		{Role: cognition.RoleAssistant, Blocks: []cognition.Block{
			{Type: cognition.BlockToolUse, ID: "toolu_9", Name: "screenshot", Input: json.RawMessage(`{}`)},
		}},
		{Role: cognition.RoleUser, Blocks: []cognition.Block{{
			Type:      cognition.BlockToolResult,
			ToolUseID: "toolu_9",
			Content: []cognition.Block{
				cognition.TextBlock("captured"),
				cognition.ImageBlock("image/png", png),
			},
		}}},
	}
	collect(t, a, turn)

	want := base64.StdEncoding.EncodeToString(png)
	messages, _ := rec.body(0)["messages"].([]any)
	if len(messages) != 3 {
		t.Fatalf("want 3 messages, got %d", len(messages))
	}

	userBlocks, _ := messages[0].(map[string]any)["content"].([]any)
	img, _ := userBlocks[1].(map[string]any)
	src, _ := img["source"].(map[string]any)
	if img["type"] != "image" || src["type"] != "base64" || src["media_type"] != "image/png" || src["data"] != want {
		t.Fatalf("user image block = %v", img)
	}

	resultBlocks, _ := messages[2].(map[string]any)["content"].([]any)
	result, _ := resultBlocks[0].(map[string]any)
	inner, _ := result["content"].([]any)
	if len(inner) != 2 {
		t.Fatalf("tool_result content = %v", inner)
	}
	innerImage, _ := inner[1].(map[string]any)
	innerSrc, _ := innerImage["source"].(map[string]any)
	if innerImage["type"] != "image" || innerSrc["data"] != want {
		t.Fatalf("tool_result image block = %v", innerImage)
	}
}

func TestAnthropicStopReasons(t *testing.T) {
	t.Run("end_turn completes", func(t *testing.T) {
		rec := newRecorder(roundSSE("all done", "end_turn"))
		a := newAdapter(rec.serve(t), "claude-opus-5")
		events := collect(t, a, simpleTurn())
		last := events[len(events)-1]
		if !last.Done || last.Truncated {
			t.Fatalf("end_turn = %+v", last)
		}
	})

	t.Run("max_tokens continues truncated", func(t *testing.T) {
		rec := newRecorder(roundSSE("half a th", "max_tokens"))
		a := newAdapter(rec.serve(t), "claude-opus-5")
		events := collect(t, a, simpleTurn())
		last := events[len(events)-1]
		if !last.Done || !last.Truncated {
			t.Fatalf("max_tokens = %+v", last)
		}
	})

	t.Run("refusal surfaces the category", func(t *testing.T) {
		refusal := sseEvent("message_start", `{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"usage":{"input_tokens":4,"output_tokens":1}}}`) +
			sseEvent("message_delta", `{"type":"message_delta","delta":{"stop_reason":"refusal","stop_details":{"type":"refusal","category":"cyber","explanation":"declined"}},"usage":{"output_tokens":1}}`) +
			sseEvent("message_stop", `{"type":"message_stop"}`)
		rec := newRecorder(refusal)
		a := newAdapter(rec.serve(t), "claude-opus-5")
		events := collect(t, a, simpleTurn())
		last := events[len(events)-1]
		if !last.Done {
			t.Fatalf("a refusal ends the turn: %+v", last)
		}
		if !strings.Contains(last.Status, "cyber") {
			t.Fatalf("refusal status = %q, want the stop_details category", last.Status)
		}
		if !strings.Contains(last.Text, "declined") {
			t.Fatalf("refusal text = %q", last.Text)
		}
		if rec.calls() != 1 {
			t.Fatalf("a refusal must not be retried, calls = %d", rec.calls())
		}
	})

	t.Run("pause_turn resends the transcript", func(t *testing.T) {
		paused := sseEvent("message_start", `{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"usage":{"input_tokens":4,"output_tokens":1}}}`) +
			sseEvent("message_delta", `{"type":"message_delta","delta":{"stop_reason":"pause_turn"},"usage":{"output_tokens":1}}`) +
			sseEvent("message_stop", `{"type":"message_stop"}`)
		rec := newRecorder(paused, roundSSE("resumed", "end_turn"))
		a := newAdapter(rec.serve(t), "claude-opus-5")
		events := collect(t, a, simpleTurn())
		if rec.calls() != 2 {
			t.Fatalf("pause_turn should resend, calls = %d", rec.calls())
		}
		var text strings.Builder
		for _, ev := range events {
			text.WriteString(ev.Text)
		}
		if text.String() != "resumed" || !events[len(events)-1].Done {
			t.Fatalf("resumed round = %q / %+v", text.String(), events[len(events)-1])
		}
		first, _ := json.Marshal(rec.body(0)["messages"])
		second, _ := json.Marshal(rec.body(1)["messages"])
		if string(first) != string(second) {
			t.Fatalf("pause_turn must resend the same transcript:\n%s\n%s", first, second)
		}
	})
}

func TestAnthropicUsageIncludesCacheCounts(t *testing.T) {
	cached := sseEvent("message_start", `{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"usage":{"input_tokens":120,"output_tokens":1,"cache_read_input_tokens":30000,"cache_creation_input_tokens":900}}}`) +
		sseEvent("message_delta", `{"type":"message_delta","delta":{"stop_reason":"end_turn"},"usage":{"input_tokens":120,"output_tokens":64,"cache_read_input_tokens":30000,"cache_creation_input_tokens":900}}`) +
		sseEvent("message_stop", `{"type":"message_stop"}`)
	rec := newRecorder(cached)
	a := newAdapter(rec.serve(t), "claude-opus-5")

	var usage *cognition.Usage
	for _, ev := range collect(t, a, simpleTurn()) {
		if ev.Usage != nil {
			usage = ev.Usage
		}
	}
	if usage == nil {
		t.Fatalf("no usage emitted")
	}
	if usage.PromptTokens != 120 || usage.CompletionTokens != 64 {
		t.Fatalf("usage = %+v", usage)
	}
	if usage.CacheReadTokens != 30000 || usage.CacheWriteTokens != 900 {
		t.Fatalf("cache accounting lost: %+v", usage)
	}
	if usage.TotalTokens != 120+64+30000+900 {
		t.Fatalf("cached input is billed input: %+v", usage)
	}
	// The usage frame is JSON-marshalled straight onto the token stream.
	raw, _ := json.Marshal(usage)
	if !strings.Contains(string(raw), `"cache_read_tokens":30000`) {
		t.Fatalf("usage frame = %s", raw)
	}
}

func TestAnthropicRetriesADroppedStreamOnce(t *testing.T) {
	// A body that stops after the first delta: no message_delta, so the round
	// never reported a stop_reason.
	dropped := sseEvent("message_start", `{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"usage":{"input_tokens":4,"output_tokens":1}}}`) +
		sseEvent("content_block_start", `{"type":"content_block_start","index":0,"content_block":{"type":"text","text":""}}`) +
		sseEvent("content_block_delta", `{"type":"content_block_delta","index":0,"delta":{"type":"text_delta","text":"partial"}}`)

	rec := newRecorder(dropped, roundSSE("complete", "end_turn"))
	a := newAdapter(rec.serve(t), "claude-opus-5")

	events := collect(t, a, simpleTurn())
	if rec.calls() != 2 {
		t.Fatalf("a dropped stream should be retried once, calls = %d", rec.calls())
	}
	if !events[len(events)-1].Done {
		t.Fatalf("the retried round should complete: %+v", events)
	}
	first, _ := json.Marshal(rec.body(0))
	second, _ := json.Marshal(rec.body(1))
	if string(first) != string(second) {
		t.Fatalf("the retry must use the same transcript")
	}
}

func TestAnthropicGivesUpAfterOneRetry(t *testing.T) {
	dropped := sseEvent("message_start", `{"type":"message_start","message":{"id":"m","type":"message","role":"assistant","model":"claude-opus-5","content":[],"stop_reason":null,"usage":{"input_tokens":4,"output_tokens":1}}}`)
	rec := newRecorder(dropped, dropped)
	a := newAdapter(rec.serve(t), "claude-opus-5")

	events := collect(t, a, simpleTurn())
	if rec.calls() != 2 {
		t.Fatalf("calls = %d, want exactly one retry", rec.calls())
	}
	for _, ev := range events {
		if ev.Done {
			t.Fatalf("a twice-dropped round must not report Done: %+v", events)
		}
	}
}

func TestAnthropicAuthFailureIsAStreamError(t *testing.T) {
	rec := newRecorder("")
	rec.statuses = []int{http.StatusUnauthorized}
	a := newAdapter(rec.serve(t), "claude-opus-5")

	_, err := a.Stream(context.Background(), simpleTurn())
	if err == nil {
		t.Fatalf("want an error the runner can fall back on")
	}
	if !strings.Contains(err.Error(), "anthropic HTTP 401") {
		t.Fatalf("error = %v", err)
	}
}

func TestAnthropicRequiresModelAndKey(t *testing.T) {
	if _, err := (&Anthropic{APIKey: "k"}).Stream(context.Background(), simpleTurn()); err == nil {
		t.Fatalf("a missing model must fail before any request")
	}
	if _, err := (&Anthropic{Model: "claude-opus-5"}).Stream(context.Background(), simpleTurn()); err == nil {
		t.Fatalf("a missing key must fail before any request")
	}
}

func TestAnthropicAPIRootNormalizesConfiguredBaseURL(t *testing.T) {
	for raw, want := range map[string]string{
		"":                              "",
		"https://api.anthropic.com/v1":  "https://api.anthropic.com/",
		"https://api.anthropic.com/v1/": "https://api.anthropic.com/",
		"https://gw.example.com":        "https://gw.example.com/",
	} {
		if got := anthropicAPIRoot(raw); got != want {
			t.Fatalf("anthropicAPIRoot(%q) = %q, want %q", raw, got, want)
		}
	}
}

func TestAnthropicBaseURLOverrideIsHonoured(t *testing.T) {
	rec := newRecorder(roundSSE("ok", "end_turn"))
	srv := rec.serve(t)
	a := &Anthropic{
		BaseURL:    srv.URL + "/v1",
		APIKey:     "sk-ant-test",
		Model:      "claude-opus-5",
		HTTPClient: srv.Client(),
	}
	collect(t, a, simpleTurn())
	if rec.calls() != 1 {
		t.Fatalf("the /v1 form must resolve to the same endpoint, calls = %d", rec.calls())
	}
}

func TestAnthropicSystemSplit(t *testing.T) {
	a := &Anthropic{}
	stable, volatile := a.splitSystem("STABLE\n\n[Turn context]\nsession=abc\n\n[Session Brief · epoch working memory]\nbrief")
	if stable != "STABLE" || !strings.Contains(volatile, "[Turn context]") || strings.Contains(stable, "session=abc") {
		t.Fatalf("turn-context split = %q / %q", stable, volatile)
	}
	stable, volatile = a.splitSystem("STABLE\n\n[Session Brief · epoch working memory]\nbrief")
	if stable != "STABLE" || !strings.HasPrefix(volatile, "[Session Brief") {
		t.Fatalf("brief split = %q / %q", stable, volatile)
	}
	stable, volatile = a.splitSystem("STABLE ONLY")
	if stable != "STABLE ONLY" || volatile != "" {
		t.Fatalf("no-marker split = %q / %q", stable, volatile)
	}
	fixed := &Anthropic{SystemStable: "PREFIX"}
	stable, volatile = fixed.splitSystem("PREFIX and then the tail")
	if stable != "PREFIX" || volatile != "and then the tail" {
		t.Fatalf("explicit split = %q / %q", stable, volatile)
	}
}

func TestAnthropicRendersATrailingUserTurn(t *testing.T) {
	rec := newRecorder(roundSSE("ok", "end_turn"))
	a := newAdapter(rec.serve(t), "claude-opus-5")
	turn := simpleTurn()
	// A transcript that ends on the assistant would be read as a prefill,
	// which every 4.6+ model rejects with a 400.
	turn.Messages = append(turn.Messages, cognition.Message{
		Role:   cognition.RoleAssistant,
		Blocks: []cognition.Block{cognition.TextBlock("thinking out loud")},
	})
	collect(t, a, turn)

	messages, _ := rec.body(0)["messages"].([]any)
	last, _ := messages[len(messages)-1].(map[string]any)
	if last["role"] != "user" {
		t.Fatalf("last rendered message role = %v", last["role"])
	}
}

func TestAnthropicDropsThinkingBlocksOnReplay(t *testing.T) {
	rec := newRecorder(roundSSE("ok", "end_turn"))
	a := newAdapter(rec.serve(t), "claude-opus-5")
	turn := simpleTurn()
	turn.Messages = append(turn.Messages,
		cognition.Message{Role: cognition.RoleAssistant, Blocks: []cognition.Block{
			cognition.ThinkingBlock("unsigned summary"),
			cognition.TextBlock("answer"),
		}},
		cognition.UserText("next"),
	)
	collect(t, a, turn)

	raw, _ := json.Marshal(rec.body(0)["messages"])
	if strings.Contains(string(raw), "unsigned summary") {
		// Replaying a thinking block without its signature is a 400.
		t.Fatalf("thinking blocks must not be replayed: %s", raw)
	}
	if !strings.Contains(string(raw), "answer") {
		t.Fatalf("assistant prose dropped: %s", raw)
	}
}

func TestAnthropicImplementsToolAdvertiser(t *testing.T) {
	var adv ToolAdvertiser = &Anthropic{}
	adv.SetTools(registrySurface())
	a := adv.(*Anthropic)
	if len(a.tools) != 3 {
		t.Fatalf("tools = %d", len(a.tools))
	}
	if a.abiName("shell_exec") != "shell.exec" {
		t.Fatalf("abiName = %q", a.abiName("shell_exec"))
	}
	if a.wireName("shell.exec") != "shell_exec" {
		t.Fatalf("wireName = %q", a.wireName("shell.exec"))
	}
	adv.SetTools(nil)
	if a.tools != nil {
		t.Fatalf("SetTools(nil) must clear the surface")
	}
}

func TestOpenAICompatImplementsToolAdvertiser(t *testing.T) {
	var adv ToolAdvertiser = &OpenAICompat{}
	adv.SetTools(registrySurface())
	oc := adv.(*OpenAICompat)
	if len(oc.Tools) != 3 {
		t.Fatalf("tools = %d", len(oc.Tools))
	}
	if oc.ResolveToolName("workspace_read") != "workspace.read" {
		t.Fatalf("name map lost: %v", oc.ToolNameMap)
	}
	adv.SetTools(nil)
	if oc.Tools != nil || oc.ToolNameMap != nil {
		t.Fatalf("SetTools(nil) must clear the surface")
	}
}

func TestAnthropicContextWindow(t *testing.T) {
	if got := (&Anthropic{Model: "claude-opus-5"}).ContextWindow(); got != anthropicFrontierWindow {
		t.Fatalf("frontier window = %d", got)
	}
	if got := (&Anthropic{Model: "claude-haiku-4-5"}).ContextWindow(); got != anthropicHaikuWindow {
		t.Fatalf("haiku window = %d", got)
	}
}
