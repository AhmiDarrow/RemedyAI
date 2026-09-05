package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func readSSE(t *testing.T, resp *http.Response) string {
	t.Helper()
	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return string(raw)
}

func TestStreamPassesSessionProjectPath(t *testing.T) {
	runner := &stubRunner{tokens: []string{"ok"}}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}

	proj := t.TempDir()
	body, _ := json.Marshal(map[string]any{"title": "P", "project_path": proj})
	req := authReq(t, http.MethodPost, base+"/api/sessions", token, bytes.NewReader(body))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("create: %d %s", resp.StatusCode, raw)
	}
	var sess ChatSession
	if err := json.Unmarshal(raw, &sess); err != nil {
		t.Fatal(err)
	}

	req = authReq(t, http.MethodPost, base+"/api/sessions/"+sess.ID+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"list"}`))
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	_ = readSSE(t, resp)
	resp.Body.Close()

	call, ok := runner.lastCall()
	if !ok {
		t.Fatal("runner not called")
	}
	if call.ProjectPath != proj {
		t.Fatalf("ProjectPath=%q want %q", call.ProjectPath, proj)
	}
}

func TestStreamHappyPathFrames(t *testing.T) {
	runner := &stubRunner{tokens: []string{"Hello ", "world"}}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	if ct := resp.Header.Get("Content-Type"); !strings.Contains(ct, "text/event-stream") {
		t.Fatalf("content-type = %q", ct)
	}
	body := readSSE(t, resp)
	if !strings.Contains(body, "event: start") {
		t.Fatalf("missing start: %s", body)
	}
	if !strings.Contains(body, `"claim_epoch"`) || !strings.Contains(body, `"request_id"`) {
		t.Fatalf("start payload incomplete: %s", body)
	}
	if !strings.Contains(body, "event: token") || !strings.Contains(body, `"text":"Hello "`) {
		t.Fatalf("missing token frames: %s", body)
	}
	if !strings.Contains(body, "event: done") || !strings.Contains(body, `"status":"ok"`) {
		t.Fatalf("missing done: %s", body)
	}

	req = authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages", token, nil)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	var listed struct {
		Messages []ChatMessage `json:"messages"`
	}
	_ = json.Unmarshal(raw, &listed)
	if len(listed.Messages) != 2 {
		t.Fatalf("want user+assistant, got %#v", listed.Messages)
	}
	if listed.Messages[1].Content != "Hello world" {
		t.Fatalf("assistant = %#v", listed.Messages[1].Content)
	}
}

func TestStreamMissingSession404(t *testing.T) {
	base, shutdown, token := startMessagesServer(t, &stubRunner{tokens: []string{"x"}}, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	req := authReq(t, http.MethodPost, base+"/api/sessions/does-not-exist/messages/stream", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 404 {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}

func TestStreamEmptyMessage400(t *testing.T) {
	base, shutdown, token := startMessagesServer(t, &stubRunner{tokens: []string{"x"}}, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	sid := createSessionID(t, client, base, token)
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"  "}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 400 || !strings.Contains(string(raw), "Message or attachment required") {
		t.Fatalf("empty => %d %s", resp.StatusCode, raw)
	}
}

func TestStreamSupersedePersistWording(t *testing.T) {
	hold := make(chan struct{})
	runner := &stubRunner{
		tokens:    []string{"partial "},
		hold:      hold,
		holdAfter: 1,
	}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	done := make(chan struct{})
	go func() {
		defer close(done)
		req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
			bytes.NewBufferString(`{"message":"go"}`))
		resp, err := client.Do(req)
		if err == nil {
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
		}
	}()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		req := authReq(t, http.MethodGet, base+"/api/turn-active", token, nil)
		resp, _ := client.Do(req)
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var b map[string]any
		_ = json.Unmarshal(raw, &b)
		if b["active"] == true {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/abort?reason=supersede", token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	close(hold)
	<-done

	deadline = time.Now().Add(2 * time.Second)
	var content string
	for time.Now().Before(deadline) {
		req = authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages", token, nil)
		resp, err = client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var listed struct {
			Messages []ChatMessage `json:"messages"`
		}
		_ = json.Unmarshal(raw, &listed)
		for _, m := range listed.Messages {
			if m.Role == "assistant" {
				content, _ = m.Content.(string)
				break
			}
		}
		if content != "" {
			break
		}
		time.Sleep(30 * time.Millisecond)
	}
	if !strings.Contains(content, "partial") {
		t.Fatalf("content = %q", content)
	}
	if !strings.Contains(content, "Interrupted by your next message") {
		t.Fatalf("want supersede note, got %q", content)
	}
	if strings.Contains(content, "Generation stopped") {
		t.Fatalf("stop note leaked into supersede row: %q", content)
	}
}

func TestStreamNoRunner503(t *testing.T) {
	base, shutdown, token := startMessagesServer(t, nil, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	sid := createSessionID(t, client, base, token)
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 503 {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}

func TestStream409BusyExactText(t *testing.T) {
	hold := make(chan struct{})
	runner := &stubRunner{tokens: []string{"slow"}, hold: hold}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	done := make(chan struct{})
	go func() {
		defer close(done)
		req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
			bytes.NewBufferString(`{"message":"a"}`))
		resp, err := client.Do(req)
		if err == nil {
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
		}
	}()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		req := authReq(t, http.MethodGet, base+"/api/turn-active", token, nil)
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		if body["active"] == true {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"b"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 409 {
		t.Fatalf("status = %d %s", resp.StatusCode, raw)
	}
	var errBody map[string]string
	_ = json.Unmarshal(raw, &errBody)
	if errBody["detail"] != sessionBusyDetail {
		t.Fatalf("409 detail = %q", errBody["detail"])
	}

	// Sync twin also 409s while stream holds the claim.
	req = authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
		bytes.NewBufferString(`{"message":"c"}`))
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 409 {
		t.Fatalf("sync busy = %d %s", resp.StatusCode, raw)
	}

	close(hold)
	<-done
}

func TestStreamAbortedEventNotError(t *testing.T) {
	runner := &stubRunner{tokens: []string{"partial ", "@@aborted\n"}}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	body := readSSE(t, resp)
	if !strings.Contains(body, "event: aborted") {
		t.Fatalf("missing aborted: %s", body)
	}
	if !strings.Contains(body, "Generation stopped") {
		t.Fatalf("missing stop message: %s", body)
	}
	if !strings.Contains(body, "claim_epoch") {
		t.Fatalf("missing claim_epoch on start: %s", body)
	}
	if strings.Contains(body, "event: error") {
		t.Fatalf("abort must not be error: %s", body)
	}
	if !strings.Contains(body, `"status":"aborted"`) {
		t.Fatalf("done status: %s", body)
	}

	req = authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages", token, nil)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	var listed struct {
		Messages []ChatMessage `json:"messages"`
	}
	_ = json.Unmarshal(raw, &listed)
	var assistant *ChatMessage
	for i := range listed.Messages {
		if listed.Messages[i].Role == "assistant" {
			assistant = &listed.Messages[i]
		}
	}
	if assistant == nil {
		t.Fatal("expected durable assistant row")
	}
	content, _ := assistant.Content.(string)
	if !strings.Contains(content, "partial") || !strings.Contains(content, "Generation stopped") {
		t.Fatalf("interrupted content = %q", content)
	}
}

func TestStreamToolFramesAndPersist(t *testing.T) {
	runner := &stubRunner{tokens: []string{
		"Looking ",
		`@@tool_call:{"name":"file_read","args":{"path":"a.py"}}`,
		`@@tool_result:{"name":"file_read","preview":"print(1)","ok":true}`,
		"@@aborted\n",
	}}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"look"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body := readSSE(t, resp)
	resp.Body.Close()
	if !strings.Contains(body, "event: tool_call") || !strings.Contains(body, `"name":"file_read"`) {
		t.Fatalf("tool_call: %s", body)
	}
	if !strings.Contains(body, "event: tool_result") || !strings.Contains(body, `"ok":true`) {
		t.Fatalf("tool_result: %s", body)
	}

	req = authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages", token, nil)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	var listed struct {
		Messages []ChatMessage `json:"messages"`
	}
	_ = json.Unmarshal(raw, &listed)
	var assistant *ChatMessage
	for i := range listed.Messages {
		if listed.Messages[i].Role == "assistant" {
			assistant = &listed.Messages[i]
		}
	}
	if assistant == nil {
		t.Fatal("no assistant")
	}
	calls, _ := assistant.ToolCalls.([]any)
	if len(calls) != 1 {
		t.Fatalf("tool_calls = %#v", assistant.ToolCalls)
	}
	m, _ := calls[0].(map[string]any)
	if m["name"] != "file_read" {
		t.Fatalf("call = %#v", m)
	}
}

func TestStreamAbortViaEndpoint(t *testing.T) {
	hold := make(chan struct{})
	runner := &stubRunner{tokens: []string{"half "}, hold: hold}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	var bodyMu sync.Mutex
	var body string
	done := make(chan struct{})
	go func() {
		defer close(done)
		req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
			bytes.NewBufferString(`{"message":"go"}`))
		resp, err := client.Do(req)
		if err != nil {
			return
		}
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		bodyMu.Lock()
		body = string(raw)
		bodyMu.Unlock()
	}()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		req := authReq(t, http.MethodGet, base+"/api/turn-active", token, nil)
		resp, _ := client.Do(req)
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var b map[string]any
		_ = json.Unmarshal(raw, &b)
		if b["active"] == true {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/abort?reason=stop", token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	var abortBody map[string]any
	_ = json.Unmarshal(raw, &abortBody)
	if abortBody["status"] != "aborted" {
		t.Fatalf("abort = %#v", abortBody)
	}
	close(hold)
	<-done

	bodyMu.Lock()
	got := body
	bodyMu.Unlock()
	if !strings.Contains(got, "event: aborted") && !strings.Contains(got, `"status":"aborted"`) {
		t.Fatalf("stream body after abort = %s", got)
	}
}

func TestStreamDisconnectDoesNotKillJob(t *testing.T) {
	var finished atomic.Bool
	hold := make(chan struct{})
	runner := &stubRunner{
		tokens: []string{
			"half an answer ",
			`@@tool_call:{"name":"list_dir","args":{"path":"."}}`,
		},
		hold:      hold,
		holdAfter: 2, // emit both tokens, then block until abort/release
	}
	wrapped := &countingRunner{inner: runner, onDone: func() { finished.Store(true) }}
	base, shutdown, token := startMessagesServer(t, wrapped, t.TempDir())
	defer shutdown()

	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"slow"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}

	seen := make(chan struct{})
	go func() {
		defer close(seen)
		buf := make([]byte, 256)
		var acc strings.Builder
		for {
			n, readErr := resp.Body.Read(buf)
			if n > 0 {
				acc.Write(buf[:n])
				if strings.Contains(acc.String(), "list_dir") {
					return
				}
			}
			if readErr != nil {
				return
			}
		}
	}()
	select {
	case <-seen:
	case <-time.After(3 * time.Second):
		t.Fatal("timeout waiting for tool_call frame")
	}
	// Drop the SSE socket — turn must keep the claim.
	_ = resp.Body.Close()

	time.Sleep(150 * time.Millisecond)
	if finished.Load() {
		t.Fatal("disconnect killed the detached job")
	}
	req = authReq(t, http.MethodGet, base+"/api/turn-active", token, nil)
	activeResp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(activeResp.Body)
	activeResp.Body.Close()
	var activeBody map[string]any
	_ = json.Unmarshal(raw, &activeBody)
	if activeBody["active"] != true {
		t.Fatalf("claim should remain after disconnect: %s", raw)
	}

	req = authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/abort?reason=stop", token, nil)
	abortResp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	abortResp.Body.Close()
	close(hold)

	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		if finished.Load() {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}
	if !finished.Load() {
		t.Fatal("job did not finish after abort")
	}

	deadline = time.Now().Add(2 * time.Second)
	var assistant *ChatMessage
	for time.Now().Before(deadline) {
		req = authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages", token, nil)
		listResp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		raw, _ = io.ReadAll(listResp.Body)
		listResp.Body.Close()
		var listed struct {
			Messages []ChatMessage `json:"messages"`
		}
		_ = json.Unmarshal(raw, &listed)
		for i := range listed.Messages {
			if listed.Messages[i].Role == "assistant" {
				assistant = &listed.Messages[i]
				break
			}
		}
		if assistant != nil {
			break
		}
		time.Sleep(30 * time.Millisecond)
	}
	if assistant == nil {
		t.Fatal("expected durable assistant row after disconnect+abort")
	}
	content, _ := assistant.Content.(string)
	if !strings.Contains(content, "half an answer") {
		t.Fatalf("content = %q", content)
	}
	if !strings.Contains(content, "Generation stopped") && !strings.Contains(content, "Used tools") {
		t.Fatalf("missing interrupt note: %q", content)
	}
}

func TestStreamDeleteReleasesClaim(t *testing.T) {
	hold := make(chan struct{})
	runner := &stubRunner{tokens: []string{"x"}, hold: hold}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 8 * time.Second}
	sid := createSessionID(t, client, base, token)

	streamDone := make(chan struct{})
	go func() {
		defer close(streamDone)
		req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
			bytes.NewBufferString(`{"message":"a"}`))
		resp, err := client.Do(req)
		if err == nil {
			_, _ = io.Copy(io.Discard, resp.Body)
			resp.Body.Close()
		}
	}()

	deadline := time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		req := authReq(t, http.MethodGet, base+"/api/turn-active", token, nil)
		resp, _ := client.Do(req)
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var b map[string]any
		_ = json.Unmarshal(raw, &b)
		if b["active"] == true {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	req := authReq(t, http.MethodDelete, base+"/api/sessions/"+sid, token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("delete = %d", resp.StatusCode)
	}
	// Unblock any hold that raced ahead of abort; claim ctx cancel is the real signal.
	close(hold)

	deadline = time.Now().Add(3 * time.Second)
	for time.Now().Before(deadline) {
		req = authReq(t, http.MethodGet, base+"/api/turn-active", token, nil)
		resp, _ = client.Do(req)
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var b map[string]any
		_ = json.Unmarshal(raw, &b)
		if b["active"] == false {
			select {
			case <-streamDone:
			case <-time.After(3 * time.Second):
				t.Fatal("stream handler did not finish after claim release")
			}
			return
		}
		time.Sleep(20 * time.Millisecond)
	}
	t.Fatal("delete did not release stream claim")
}

func TestStreamThinkingAndProgressFrames(t *testing.T) {
	runner := &stubRunner{tokens: []string{
		"@@thinking_round",
		"@@thinking:scratch",
		"@@status:Decoding…",
		"ok",
	}}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages/stream", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	body := readSSE(t, resp)
	resp.Body.Close()
	if !strings.Contains(body, "event: thinking") || !strings.Contains(body, `"replace":true`) {
		t.Fatalf("thinking replace: %s", body)
	}
	if !strings.Contains(body, "event: progress") || !strings.Contains(body, "Decoding") {
		t.Fatalf("progress: %s", body)
	}
}

func TestInterruptedTurnContentHelpers(t *testing.T) {
	if interruptedTurnContent("", "", false) != stopNote {
		t.Fatal("stop note")
	}
	if interruptedTurnContent("", "supersede", false) != supersedeNote {
		t.Fatal("supersede note")
	}
	body := interruptedTurnContent("partial text", "supersede", true)
	if !strings.HasPrefix(body, "partial text") || !strings.HasSuffix(body, supersedeNote) {
		t.Fatalf("body = %q", body)
	}
	toolsOnly := interruptedTurnContent("", "stop", true)
	if !strings.Contains(toolsOnly, "Used tools") || !strings.HasSuffix(toolsOnly, stopNote) {
		t.Fatalf("toolsOnly = %q", toolsOnly)
	}
}

func TestFixtureTurnRunnerDefault(t *testing.T) {
	r := NewFixtureTurnRunner()
	var out strings.Builder
	err := r.RunTurn(context.Background(), TurnRequest{Prompt: "hi"}, func(tok string) error {
		out.WriteString(tok)
		return nil
	})
	if err != nil {
		t.Fatal(err)
	}
	if out.String() != "Hello world" {
		t.Fatalf("got %q", out.String())
	}
}

type countingRunner struct {
	inner  TurnRunner
	onDone func()
}

func (c *countingRunner) RunTurn(ctx context.Context, req TurnRequest, emit func(string) error) error {
	defer func() {
		if c.onDone != nil {
			c.onDone()
		}
	}()
	return c.inner.RunTurn(ctx, req, emit)
}
