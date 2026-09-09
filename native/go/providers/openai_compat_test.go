package providers

import (
	"context"
	"encoding/base64"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

// userTurn is a one-message transcript: the user's request.
func userTurn(text string) cognition.Turn {
	return cognition.Turn{Messages: []cognition.Message{cognition.UserText(text)}}
}

func TestRenderMessagesPrependsSystem(t *testing.T) {
	turn := userTurn("hi")
	turn.System = "You are Remedy."
	msgs := renderMessages(turn, nil)
	if len(msgs) < 2 {
		t.Fatalf("msgs=%v", msgs)
	}
	if msgs[0]["role"] != "system" || msgs[0]["content"] != "You are Remedy." {
		t.Fatalf("system msg=%v", msgs[0])
	}
	if msgs[1]["role"] != "user" || msgs[1]["content"] != "hi" {
		t.Fatalf("user msg=%v", msgs[1])
	}
}

func TestOpenAICompatStreamsTextAndDone(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if r.URL.Path != "/v1/chat/completions" {
			t.Fatalf("path %s", r.URL.Path)
		}
		if r.Header.Get("Authorization") != "Bearer sk-test-not-a-real-key-abcdef" {
			t.Fatalf("auth %q", r.Header.Get("Authorization"))
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"Hello \"}}]}\n\n"))
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"world\"},\"finish_reason\":\"stop\"}]}\n\n"))
		_, _ = w.Write([]byte("data: [DONE]\n\n"))
	}))
	defer srv.Close()

	m := &OpenAICompat{
		BaseURL:    srv.URL + "/v1",
		APIKey:     "sk-test-not-a-real-key-abcdef",
		Model:      "gpt-4o-mini",
		HTTPClient: srv.Client(),
	}
	ch, err := m.Stream(context.Background(), userTurn("hi"))
	if err != nil {
		t.Fatal(err)
	}
	var text strings.Builder
	done := false
	for ev := range ch {
		text.WriteString(ev.Text)
		done = done || ev.Done
	}
	if text.String() != "Hello world" || !done {
		t.Fatalf("text=%q done=%v", text.String(), done)
	}
}

func TestOpenAICompatHTTPError(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, `{"error":"nope"}`, http.StatusUnauthorized)
	}))
	defer srv.Close()
	m := &OpenAICompat{BaseURL: srv.URL + "/v1", APIKey: "sk-test-not-a-real-key-abcdef", Model: "x", HTTPClient: srv.Client()}
	_, err := m.Stream(context.Background(), userTurn("x"))
	if err == nil || !strings.Contains(err.Error(), "401") {
		t.Fatalf("err=%v", err)
	}
}

func TestOpenAICompatCancelMidStream(t *testing.T) {
	started := make(chan struct{})
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		flusher, _ := w.(http.Flusher)
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"partial\"}}]}\n\n"))
		if flusher != nil {
			flusher.Flush()
		}
		close(started)
		time.Sleep(2 * time.Second)
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{},\"finish_reason\":\"stop\"}]}\n\n"))
	}))
	defer srv.Close()

	ctx, cancel := context.WithCancel(context.Background())
	m := &OpenAICompat{BaseURL: srv.URL + "/v1", APIKey: "unused", Model: "x", HTTPClient: srv.Client()}
	ch, err := m.Stream(ctx, userTurn("x"))
	if err != nil {
		t.Fatal(err)
	}
	<-started
	cancel()
	deadline := time.After(2 * time.Second)
	for {
		select {
		case _, ok := <-ch:
			if !ok {
				return
			}
		case <-deadline:
			t.Fatal("channel did not close after cancel")
		}
	}
}

func TestSanitizeToolName(t *testing.T) {
	cases := map[string]string{
		"files.list":    "files_list",
		"computer_snap": "computer_snap",
		"web-fetch":     "web-fetch",
		"a.b.c":         "a_b_c",
		"":              "tool",
		"9bad":          "t_9bad",
		"files list":    "files_list",
	}
	for in, want := range cases {
		if got := sanitizeToolName(in); got != want {
			t.Fatalf("sanitizeToolName(%q)=%q want %q", in, got, want)
		}
	}
}

func TestOpenAICompatResolveToolName(t *testing.T) {
	m := &OpenAICompat{ToolNameMap: map[string]string{"files_list": "files.list"}}
	if got := m.ResolveToolName("files_list"); got != "files.list" {
		t.Fatalf("ResolveToolName=%q", got)
	}
	if got := m.ResolveToolName("other"); got != "other" {
		t.Fatalf("passthrough=%q", got)
	}
}

func TestRenderMessagesIncludesToolCallIDs(t *testing.T) {
	msgs := renderMessages(cognition.Turn{Messages: []cognition.Message{
		cognition.UserText("list files"),
		{Role: cognition.RoleAssistant, Blocks: []cognition.Block{
			{Type: cognition.BlockToolUse, ID: "call_1", Name: "files_list", Input: []byte(`{"path":"."}`)},
		}},
		{Role: cognition.RoleUser, Blocks: []cognition.Block{
			{Type: cognition.BlockToolResult, ToolUseID: "call_1", Content: []cognition.Block{cognition.TextBlock(`["a"]`)}},
		}},
	}}, nil)
	if len(msgs) < 3 {
		t.Fatalf("msgs=%v", msgs)
	}
	toolCalls, ok := msgs[1]["tool_calls"].([]map[string]any)
	if !ok || len(toolCalls) != 1 {
		t.Fatalf("assistant tool_calls=%T %#v", msgs[1]["tool_calls"], msgs[1])
	}
	if toolCalls[0]["id"] != "call_1" {
		t.Fatalf("tool call id=%v", toolCalls[0]["id"])
	}
	if msgs[2]["role"] != "tool" || msgs[2]["tool_call_id"] != "call_1" {
		t.Fatalf("tool msg=%v", msgs[2])
	}
}

func TestFitLocalRequestShrinksOverBudget(t *testing.T) {
	sys := strings.Repeat("SYSTEM ", 4000)
	msgs := []map[string]any{
		{"role": "system", "content": sys},
		{"role": "user", "content": "build"},
		{"role": "tool", "content": strings.Repeat("BODY ", 2000)},
	}
	tools := []map[string]any{
		{"type": "function", "function": map[string]any{"name": "workspace_read", "description": "r", "parameters": map[string]any{}}},
		{"type": "function", "function": map[string]any{"name": "calendar_list", "description": "c", "parameters": map[string]any{}}},
	}
	outM, outT, meta := FitLocalRequest(msgs, tools, 4096)
	if meta["est_after"].(int) >= meta["est_before"].(int) && meta["est_before"].(int) > meta["prompt_budget"].(int) {
		// Must have attempted shrink levels when over budget.
		levels, _ := meta["levels"].([]string)
		if len(levels) == 0 || levels[0] == "ok" {
			t.Fatalf("meta=%v", meta)
		}
	}
	if len(outT) == 0 {
		t.Fatal("must keep some tools")
	}
	_ = outM
}

func TestRenderMessagesNeverEmitsOrphanToolRole(t *testing.T) {
	// A tool_result whose tool_use was compacted away is folded into the user
	// turn: an unannounced role=tool is HTTP 400 on every compatible provider.
	msgs := renderMessages(cognition.Turn{Messages: []cognition.Message{
		cognition.UserText("continue"),
		{Role: cognition.RoleUser, Blocks: []cognition.Block{
			{Type: cognition.BlockToolResult, ToolUseID: "orphan", Content: []cognition.Block{cognition.TextBlock("huge body")}},
		}},
	}}, nil)
	for _, m := range msgs {
		if m["role"] == "tool" {
			t.Fatalf("orphan role=tool is forbidden: %#v", m)
		}
	}
	last := msgs[len(msgs)-1]
	if last["role"] != "user" || !strings.Contains(fmt.Sprint(last["content"]), "huge body") {
		t.Fatalf("orphan result must survive as user text: %#v", last)
	}
}

func TestOpenAICompatToolCalls(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		frames := []string{
			`data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","function":{"name":"file_read","arguments":"{\"p"}}]}}]}`,
			`data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"ath\":\"a.py\"}"}}]},"finish_reason":"tool_calls"}]}`,
			`data: [DONE]`,
		}
		for _, f := range frames {
			_, _ = w.Write([]byte(f + "\n\n"))
		}
	}))
	defer srv.Close()
	m := &OpenAICompat{BaseURL: srv.URL + "/v1", APIKey: "unused", Model: "x", HTTPClient: srv.Client()}
	ch, err := m.Stream(context.Background(), userTurn("read"))
	if err != nil {
		t.Fatal(err)
	}
	var call *cognition.ToolCall
	for ev := range ch {
		if ev.ToolCall != nil {
			call = ev.ToolCall
		}
	}
	if call == nil || call.Name != "file_read" || !strings.Contains(string(call.Input), "a.py") {
		t.Fatalf("call=%#v", call)
	}
}

func TestOpenAICompatAdvertisesToolsArray(t *testing.T) {
	var sawTools bool
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer r.Body.Close()
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode: %v", err)
		}
		tools, ok := body["tools"].([]any)
		if !ok || len(tools) == 0 {
			t.Fatalf("tools missing: %#v", body["tools"])
		}
		first, _ := tools[0].(map[string]any)
		fn, _ := first["function"].(map[string]any)
		if fn["name"] != "workspace_read" {
			t.Fatalf("tool name=%v (want sanitized workspace_read)", fn["name"])
		}
		sawTools = true
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"ok\"},\"finish_reason\":\"stop\"}]}\n\n"))
	}))
	defer srv.Close()

	schemas, nameMap := ToolSchemasFromRegistryMapped([]RegistryTool{{
		ID:          "workspace.read",
		Description: "Read a file",
		InputSchema: json.RawMessage(`{"type":"object","required":["path"],"properties":{"path":{"type":"string"}}}`),
	}})
	if nameMap["workspace_read"] != "workspace.read" {
		t.Fatalf("nameMap=%v", nameMap)
	}
	m := &OpenAICompat{
		BaseURL:     srv.URL + "/v1",
		APIKey:      "unused",
		Model:       "x",
		HTTPClient:  srv.Client(),
		Tools:       schemas,
		ToolNameMap: nameMap,
	}
	ch, err := m.Stream(context.Background(), userTurn("read"))
	if err != nil {
		t.Fatal(err)
	}
	for range ch {
	}
	if !sawTools {
		t.Fatal("handler did not observe tools array")
	}
}

func TestOpenAICompatSkipsMalformed(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte("data: not-json\n\n"))
		_, _ = w.Write([]byte(": keepalive\n\n"))
		_, _ = w.Write([]byte("data: {\"choices\":[{\"delta\":{\"content\":\"ok\"},\"finish_reason\":\"stop\"}]}\n\n"))
	}))
	defer srv.Close()
	m := &OpenAICompat{BaseURL: srv.URL + "/v1", APIKey: "unused", Model: "x", HTTPClient: srv.Client()}
	ch, err := m.Stream(context.Background(), userTurn("x"))
	if err != nil {
		t.Fatal(err)
	}
	var text strings.Builder
	for ev := range ch {
		text.WriteString(ev.Text)
	}
	if text.String() != "ok" {
		t.Fatalf("got %q", text.String())
	}
}

func collectSSE(t *testing.T, body string) []cognition.ModelEvent {
	t.Helper()
	out := make(chan cognition.ModelEvent, 64)
	parseSSE(context.Background(), strings.NewReader(body), out)
	close(out)
	var evs []cognition.ModelEvent
	for ev := range out {
		evs = append(evs, ev)
	}
	return evs
}

func sseToolFrames(finish string) string {
	return `data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_9","function":{"name":"shell_exec","arguments":"{\"argv\":"}}]}}]}` + "\n\n" +
		`data: {"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"[\"echo\"]}"}}]},"finish_reason":` + finish + `}]}` + "\n\n"
}

func TestParseSSEFlushesToolCallsOnStopAndLength(t *testing.T) {
	for _, reason := range []string{`"stop"`, `"length"`, `"tool_calls"`} {
		evs := collectSSE(t, sseToolFrames(reason)+"data: [DONE]\n\n")
		var call *cognition.ToolCall
		done := false
		for _, ev := range evs {
			if ev.ToolCall != nil {
				call = ev.ToolCall
			}
			done = done || ev.Done
		}
		if call == nil || call.ID != "call_9" || call.Name != "shell_exec" || string(call.Input) != `{"argv":["echo"]}` {
			t.Fatalf("finish=%s call=%#v", reason, call)
		}
		if reason != `"tool_calls"` && !done {
			t.Fatalf("finish=%s must also emit Done", reason)
		}
	}
}

func TestParseSSEFlushesToolCallsOnDoneWithoutFinish(t *testing.T) {
	body := `data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"c1","function":{"name":"workspace_read","arguments":"{}"}}]}}]}` + "\n\n" + "data: [DONE]\n\n"
	evs := collectSSE(t, body)
	if len(evs) != 2 || evs[0].ToolCall == nil || evs[0].ToolCall.Name != "workspace_read" || !evs[1].Done {
		t.Fatalf("events=%#v", evs)
	}
	// Plain EOF (no [DONE]) still flushes the accumulated call, without Done.
	evs = collectSSE(t, strings.TrimSuffix(body, "data: [DONE]\n\n"))
	if len(evs) != 1 || evs[0].ToolCall == nil || evs[0].Done {
		t.Fatalf("eof events=%#v", evs)
	}
}

func TestParseSSEScannerErrorIsIncomplete(t *testing.T) {
	huge := `data: {"choices":[{"delta":{"content":"` + strings.Repeat("x", 1100*1024) + `"},"finish_reason":"stop"}]}` + "\n\n"
	evs := collectSSE(t, `data: {"choices":[{"delta":{"content":"start"}}]}`+"\n\n"+huge+"data: [DONE]\n\n")
	if len(evs) != 1 || evs[0].Text != "start" {
		t.Fatalf("events=%#v", evs)
	}
	for _, ev := range evs {
		if ev.Done {
			t.Fatal("oversized frame must not complete the round")
		}
	}
}

func TestParseSSEReasoningContentBecomesThinking(t *testing.T) {
	body := `data: {"choices":[{"delta":{"reasoning_content":"let me "}}]}` + "\n\n" +
		`data: {"choices":[{"delta":{"reasoning":"think"}}]}` + "\n\n" +
		`data: {"choices":[{"delta":{"content":"answer"},"finish_reason":"stop"}]}` + "\n\n" + "data: [DONE]\n\n"
	evs := collectSSE(t, body)
	var thinking, text strings.Builder
	for _, ev := range evs {
		thinking.WriteString(ev.Thinking)
		text.WriteString(ev.Text)
	}
	if thinking.String() != "let me think" || text.String() != "answer" {
		t.Fatalf("thinking=%q text=%q", thinking.String(), text.String())
	}
}

func TestOpenAICompatRequestsAndEmitsUsage(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer r.Body.Close()
		var body map[string]any
		if err := json.NewDecoder(r.Body).Decode(&body); err != nil {
			t.Fatalf("decode: %v", err)
		}
		opts, _ := body["stream_options"].(map[string]any)
		if opts["include_usage"] != true {
			t.Fatalf("stream_options=%#v", body["stream_options"])
		}
		w.Header().Set("Content-Type", "text/event-stream")
		_, _ = w.Write([]byte(`data: {"choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}` + "\n\n"))
		_, _ = w.Write([]byte(`data: {"choices":[],"usage":{"prompt_tokens":12,"completion_tokens":3,"total_tokens":15}}` + "\n\n"))
		_, _ = w.Write([]byte("data: [DONE]\n\n"))
	}))
	defer srv.Close()
	m := &OpenAICompat{BaseURL: srv.URL + "/v1", APIKey: "unused", Model: "x", HTTPClient: srv.Client()}
	ch, err := m.Stream(context.Background(), userTurn("x"))
	if err != nil {
		t.Fatal(err)
	}
	var usage *cognition.Usage
	done := false
	for ev := range ch {
		if ev.Usage != nil {
			usage = ev.Usage
		}
		done = done || ev.Done
	}
	if !done || usage == nil || usage.PromptTokens != 12 || usage.CompletionTokens != 3 || usage.TotalTokens != 15 {
		t.Fatalf("done=%v usage=%#v", done, usage)
	}
}

func TestRenderMessagesEchoesAdvertisedToolName(t *testing.T) {
	// tool_use blocks carry the ABI id; the adapter's own name map turns it
	// back into the function name the provider actually saw.
	oc := &OpenAICompat{ToolNameMap: map[string]string{"shell_exec": "shell.exec"}}
	msgs := renderMessages(cognition.Turn{Messages: []cognition.Message{
		cognition.UserText("run"),
		{Role: cognition.RoleAssistant, Blocks: []cognition.Block{
			{Type: cognition.BlockThinking, Text: "secret reasoning"},
			{Type: cognition.BlockToolUse, ID: "c1", Name: "shell.exec", Input: []byte(`{}`)},
		}},
		{Role: cognition.RoleUser, Blocks: []cognition.Block{
			{Type: cognition.BlockToolResult, ToolUseID: "c1", Content: []cognition.Block{cognition.TextBlock("ok")}},
		}},
	}}, oc.advertisedName())
	for _, m := range msgs {
		if strings.Contains(fmt.Sprint(m["content"]), "secret reasoning") {
			t.Fatalf("thinking must not go on the wire: %#v", m)
		}
	}
	var assistant map[string]any
	for _, m := range msgs {
		if m["role"] == "assistant" {
			assistant = m
		}
	}
	calls, _ := assistant["tool_calls"].([]map[string]any)
	if len(calls) != 1 {
		t.Fatalf("assistant=%#v", assistant)
	}
	fn, _ := calls[0]["function"].(map[string]any)
	if fn["name"] != "shell_exec" {
		t.Fatalf("wire name=%v want advertised shell_exec", fn["name"])
	}
}

func TestClipStringRuneSafe(t *testing.T) {
	s := strings.Repeat("日本語", 2_000)
	for _, max := range []int{50, 301, 1_000} {
		if got := clipString(s, max); !utf8.ValidString(got) {
			t.Fatalf("max=%d split a rune", max)
		}
	}
}

func TestRenderMessagesCarriesHistoryAndAttachmentImages(t *testing.T) {
	png := []byte{0x89, 'P', 'N', 'G'}
	msgs := renderMessages(cognition.Turn{Messages: []cognition.Message{
		cognition.UserText("what is in this file?"),
		{Role: cognition.RoleAssistant, Blocks: []cognition.Block{cognition.TextBlock("a config")}},
		{Role: cognition.RoleUser, Blocks: []cognition.Block{
			cognition.TextBlock("and this screenshot?"),
			cognition.ImageBlock("image/png", png),
		}},
	}}, nil)
	if len(msgs) != 3 {
		t.Fatalf("msgs=%#v", msgs)
	}
	if msgs[0]["content"] != "what is in this file?" || msgs[1]["role"] != "assistant" {
		t.Fatalf("history lost: %#v", msgs)
	}
	parts, ok := msgs[2]["content"].([]map[string]any)
	if !ok || len(parts) != 2 {
		t.Fatalf("image message must use content parts: %#v", msgs[2]["content"])
	}
	image, _ := parts[1]["image_url"].(map[string]any)
	if !strings.HasPrefix(fmt.Sprint(image["url"]), "data:image/png;base64,") {
		t.Fatalf("image part=%#v", parts[1])
	}
	if !strings.Contains(fmt.Sprint(image["url"]), base64.StdEncoding.EncodeToString(png)) {
		t.Fatalf("image bytes lost: %#v", parts[1])
	}
}

func TestRenderMessagesToolResultImageTrailsAsUserMessage(t *testing.T) {
	// A role=tool message cannot carry an image, so a screenshot result becomes
	// a trailing user message in the OpenAI vision shape.
	msgs := renderMessages(cognition.Turn{Messages: []cognition.Message{
		cognition.UserText("look at the screen"),
		{Role: cognition.RoleAssistant, Blocks: []cognition.Block{
			{Type: cognition.BlockToolUse, ID: "s1", Name: "computer.screenshot", Input: []byte(`{}`)},
		}},
		{Role: cognition.RoleUser, Blocks: []cognition.Block{
			{Type: cognition.BlockToolResult, ToolUseID: "s1", Content: []cognition.Block{
				cognition.TextBlock(`{"path":"a.png"}`),
				cognition.ImageBlock("image/png", []byte{1, 2, 3}),
			}},
		}},
	}}, nil)
	tool := msgs[len(msgs)-2]
	if tool["role"] != "tool" || tool["tool_call_id"] != "s1" {
		t.Fatalf("tool message=%#v", tool)
	}
	if strings.Contains(fmt.Sprint(tool["content"]), "base64") {
		t.Fatalf("tool messages must stay text: %#v", tool)
	}
	trailing := msgs[len(msgs)-1]
	parts, ok := trailing["content"].([]map[string]any)
	if trailing["role"] != "user" || !ok || len(parts) != 2 {
		t.Fatalf("trailing user image message=%#v", trailing)
	}
	if parts[1]["type"] != "image_url" {
		t.Fatalf("parts=%#v", parts)
	}
}

func TestRenderMessagesMarksErrorResults(t *testing.T) {
	msgs := renderMessages(cognition.Turn{Messages: []cognition.Message{
		cognition.UserText("run it"),
		{Role: cognition.RoleAssistant, Blocks: []cognition.Block{
			{Type: cognition.BlockToolUse, ID: "c1", Name: "shell.exec", Input: []byte(`{}`)},
		}},
		{Role: cognition.RoleUser, Blocks: []cognition.Block{
			{Type: cognition.BlockToolResult, ToolUseID: "c1", IsError: true, Content: []cognition.Block{cognition.TextBlock("boom")}},
		}},
	}}, nil)
	last := msgs[len(msgs)-1]
	if last["role"] != "tool" || !strings.Contains(fmt.Sprint(last["content"]), "[error] boom") {
		t.Fatalf("error result=%#v", last)
	}
}

func TestRenderMessagesEmptyTranscriptStillAsks(t *testing.T) {
	msgs := renderMessages(cognition.Turn{System: "sys"}, nil)
	if len(msgs) != 2 || msgs[1]["role"] != "user" || msgs[1]["content"] != "continue" {
		t.Fatalf("msgs=%#v", msgs)
	}
}

func TestOpenAICompatContextWindow(t *testing.T) {
	t.Setenv("REMEDY_LOCAL_CTX", "")
	t.Setenv("REMEDY_N_CTX", "")
	cloud := &OpenAICompat{BaseURL: "https://api.example.com/v1", Model: "x"}
	if got := cloud.ContextWindow(); got != cognition.DefaultContextWindow {
		t.Fatalf("cloud window=%d", got)
	}
	local := &OpenAICompat{BaseURL: "http://127.0.0.1:8741/v1", Model: "x"}
	if got := local.ContextWindow(); got != LocalContextWindow {
		t.Fatalf("local window=%d", got)
	}
	pinned := &OpenAICompat{BaseURL: "http://127.0.0.1:8741/v1", Model: "x", NCtx: 32768}
	if got := pinned.ContextWindow(); got != 32768 {
		t.Fatalf("pinned window=%d", got)
	}
}

func TestFitLocalRequestStillShrinksTranscriptShape(t *testing.T) {
	turn := cognition.Turn{System: strings.Repeat("SYSTEM ", 4000), Messages: []cognition.Message{
		cognition.UserText("build"),
		{Role: cognition.RoleAssistant, Blocks: []cognition.Block{
			{Type: cognition.BlockToolUse, ID: "c1", Name: "workspace.read", Input: []byte(`{"path":"a"}`)},
		}},
		{Role: cognition.RoleUser, Blocks: []cognition.Block{
			{Type: cognition.BlockToolResult, ToolUseID: "c1", Content: []cognition.Block{cognition.TextBlock(strings.Repeat("BODY ", 2000))}},
		}},
	}}
	msgs := renderMessages(turn, nil)
	tools := []map[string]any{
		{"type": "function", "function": map[string]any{"name": "workspace_read", "description": "r", "parameters": map[string]any{}}},
		{"type": "function", "function": map[string]any{"name": "calendar_list", "description": "c", "parameters": map[string]any{}}},
	}
	outM, outT, meta := FitLocalRequest(msgs, tools, 4096)
	if meta["est_after"].(int) >= meta["est_before"].(int) {
		t.Fatalf("meta=%v", meta)
	}
	if len(outT) == 0 || len(outM) != len(msgs) {
		t.Fatalf("tools=%d msgs=%d", len(outT), len(outM))
	}
	var sawTool bool
	for _, m := range outM {
		if m["role"] == "tool" {
			sawTool = true
			if len(fmt.Sprint(m["content"])) >= 10_000 {
				t.Fatalf("tool body not clipped: %d", len(fmt.Sprint(m["content"])))
			}
		}
	}
	if !sawTool {
		t.Fatal("tool message lost")
	}
}

func TestAdvertisedNameFallsBackToTheSanitizer(t *testing.T) {
	// The runner narrows the advertised tool set mid-build; a tool_use from an
	// earlier round must still go back out under the name the provider saw.
	oc := &OpenAICompat{ToolNameMap: map[string]string{"workspace_read": "workspace.read"}}
	name := oc.advertisedName()
	if got := name("workspace.read"); got != "workspace_read" {
		t.Fatalf("mapped=%q", got)
	}
	if got := name("calendar.create_event"); got != "calendar_create_event" {
		t.Fatalf("fallback=%q", got)
	}
	if (&OpenAICompat{}).advertisedName() != nil {
		t.Fatal("no name map means the model already emitted wire names")
	}
}
