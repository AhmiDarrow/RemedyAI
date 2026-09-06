package providers

import (
	"context"
	"encoding/json"
	"fmt"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func TestBuildMessagesPrependsSystem(t *testing.T) {
	msgs := buildMessages(cognition.Turn{
		System: "You are Remedy.",
		Goal:   "hi",
	})
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
	ch, err := m.Stream(context.Background(), cognition.Turn{Goal: "hi"})
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
	_, err := m.Stream(context.Background(), cognition.Turn{Goal: "x"})
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
	ch, err := m.Stream(ctx, cognition.Turn{Goal: "x"})
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
		"files.list":     "files_list",
		"computer_snap":  "computer_snap",
		"web-fetch":      "web-fetch",
		"a.b.c":          "a_b_c",
		"":               "tool",
		"9bad":           "t_9bad",
		"files list":     "files_list",
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

func TestBuildMessagesIncludesToolCallIDs(t *testing.T) {
	msgs := buildMessages(cognition.Turn{
		Goal: "list files",
		Calls: []cognition.ToolCall{{
			ID: "call_1", Name: "files_list", Input: []byte(`{"path":"."}`),
		}},
		Results: []cognition.ToolResult{{
			ID: "call_1", Name: "files_list", Output: []byte(`["a"]`),
		}},
	})
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

func TestBuildMessagesNeverEmitsOrphanToolRole(t *testing.T) {
	msgs := buildMessages(cognition.Turn{
		Goal: "continue",
		Text: "checkpoint",
		Results: []cognition.ToolResult{{
			ID: "orphan", Name: "workspace.read", Output: []byte("huge body"),
		}},
	})
	for _, m := range msgs {
		if m["role"] == "tool" {
			t.Fatalf("orphan role=tool without Calls is forbidden: %#v", m)
		}
	}
	last := msgs[len(msgs)-1]
	if last["role"] != "assistant" {
		t.Fatalf("want assistant fold, got %#v", last)
	}
	if !strings.Contains(fmt.Sprint(last["content"]), "Working memory") {
		t.Fatalf("content=%v", last["content"])
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
	ch, err := m.Stream(context.Background(), cognition.Turn{Goal: "read"})
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
	ch, err := m.Stream(context.Background(), cognition.Turn{Goal: "read"})
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
	ch, err := m.Stream(context.Background(), cognition.Turn{Goal: "x"})
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
