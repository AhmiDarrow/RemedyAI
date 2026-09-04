package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

type stubRunner struct {
	mu       sync.Mutex
	tokens   []string
	err      error
	calls    []TurnRequest
	onToken  func(i int, req TurnRequest)
	hold     chan struct{} // if set, block until closed (for 409 tests)
	holdOnce sync.Once
}

func (s *stubRunner) RunTurn(ctx context.Context, req TurnRequest, emit func(string) error) error {
	s.mu.Lock()
	s.calls = append(s.calls, req)
	tokens := append([]string(nil), s.tokens...)
	err := s.err
	onToken := s.onToken
	hold := s.hold
	s.mu.Unlock()

	if hold != nil {
		select {
		case <-hold:
		case <-ctx.Done():
			return ctx.Err()
		}
	}
	for i, tok := range tokens {
		if onToken != nil {
			onToken(i, req)
		}
		if err := emit(tok); err != nil {
			return err
		}
		select {
		case <-ctx.Done():
			return ctx.Err()
		default:
		}
	}
	return err
}

func (s *stubRunner) lastCall() (TurnRequest, bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if len(s.calls) == 0 {
		return TurnRequest{}, false
	}
	return s.calls[len(s.calls)-1], true
}

func startMessagesServer(t *testing.T, runner TurnRunner, home string) (base string, shutdown func(), token string) {
	t.Helper()
	token = "test-token-not-a-secret-16"
	dbPath := filepath.Join(t.TempDir(), "memory.db")
	cfg := Config{
		Token:      token,
		Version:    "0.50.2",
		DBPath:     dbPath,
		HomeDir:    home,
		TurnRunner: runner,
	}
	base, shutdown = startTestServer(t, cfg)
	return base, shutdown, token
}

func authReq(t *testing.T, method, url, token string, body io.Reader) *http.Request {
	t.Helper()
	req, err := http.NewRequest(method, url, body)
	if err != nil {
		t.Fatal(err)
	}
	req.Header.Set("Authorization", "Bearer "+token)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	return req
}

func createSessionID(t *testing.T, client *http.Client, base, token string) string {
	t.Helper()
	req := authReq(t, http.MethodPost, base+"/api/sessions", token, bytes.NewBufferString(`{"title":"T"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	raw, _ := io.ReadAll(resp.Body)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("create session: %d %s", resp.StatusCode, raw)
	}
	var sess ChatSession
	if err := json.Unmarshal(raw, &sess); err != nil {
		t.Fatal(err)
	}
	return sess.ID
}

func TestListMessagesWireShapeAndCaps(t *testing.T) {
	base, shutdown, token := startMessagesServer(t, &stubRunner{tokens: []string{"ok"}}, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	// Seed via send
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("send status %d", resp.StatusCode)
	}

	req = authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages", token, nil)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("list status %d %s", resp.StatusCode, raw)
	}
	var body struct {
		Messages []ChatMessage `json:"messages"`
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Messages) != 2 {
		t.Fatalf("want 2 messages, got %#v", body.Messages)
	}
	if body.Messages[0].Role != "user" || body.Messages[0].Content != "hi" {
		t.Fatalf("user row = %#v", body.Messages[0])
	}
	if body.Messages[1].Role != "assistant" || body.Messages[1].Content != "ok" {
		t.Fatalf("assistant row = %#v", body.Messages[1])
	}
	if body.Messages[0].ID == "" || body.Messages[0].CreatedAt == nil {
		t.Fatalf("missing id/created_at: %#v", body.Messages[0])
	}
	if body.Messages[0].Reverted {
		t.Fatal("reverted should be false")
	}
}

func TestListMessagesUnknownSession404(t *testing.T) {
	base, shutdown, token := startMessagesServer(t, nil, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	req := authReq(t, http.MethodGet, base+"/api/sessions/nope/messages", token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != 404 {
		t.Fatalf("status = %d", resp.StatusCode)
	}
}

func TestListMessagesInvalidPage422(t *testing.T) {
	base, shutdown, token := startMessagesServer(t, nil, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	sid := createSessionID(t, client, base, token)
	for _, qs := range []string{"limit=501", "offset=-1", "limit=abc"} {
		req := authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages?"+qs, token, nil)
		resp, err := client.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		resp.Body.Close()
		if resp.StatusCode != 422 {
			t.Fatalf("%s => %d", qs, resp.StatusCode)
		}
	}
}

func TestListMessagesContentCap(t *testing.T) {
	home := t.TempDir()
	runner := &stubRunner{tokens: []string{strings.Repeat("x", 32_050)}}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()

	req = authReq(t, http.MethodGet, base+"/api/sessions/"+sid+"/messages", token, nil)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	var body struct {
		Messages []ChatMessage `json:"messages"`
	}
	_ = json.Unmarshal(raw, &body)
	content, _ := body.Messages[1].Content.(string)
	if !strings.HasSuffix(content, "[truncated 50 chars]") {
		t.Fatalf("cap suffix missing: %q…", content[len(content)-40:])
	}
	if !strings.HasPrefix(content, strings.Repeat("x", 100)) {
		t.Fatal("cap should keep prefix")
	}
}

func TestSendMessageGuards(t *testing.T) {
	home := t.TempDir()
	base, shutdown, token := startMessagesServer(t, nil, home) // no runner
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 503 {
		t.Fatalf("no runtime => %d %s", resp.StatusCode, raw)
	}

	base2, shutdown2, token2 := startMessagesServer(t, &stubRunner{tokens: []string{"a"}}, home)
	defer shutdown2()
	sid2 := createSessionID(t, client, base2, token2)
	req = authReq(t, http.MethodPost, base2+"/api/sessions/"+sid2+"/messages", token2,
		bytes.NewBufferString(`{"message":"   "}`))
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 400 || !strings.Contains(string(raw), "Message is empty") {
		t.Fatalf("empty => %d %s", resp.StatusCode, raw)
	}
}

func TestSendMessage409BusyText(t *testing.T) {
	hold := make(chan struct{})
	runner := &stubRunner{tokens: []string{"slow"}, hold: hold}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	done := make(chan struct{})
	go func() {
		defer close(done)
		req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
			bytes.NewBufferString(`{"message":"a"}`))
		resp, err := client.Do(req)
		if err == nil {
			resp.Body.Close()
		}
	}()

	// Wait until claim is held.
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

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
		bytes.NewBufferString(`{"message":"b"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 409 {
		t.Fatalf("busy status = %d %s", resp.StatusCode, raw)
	}
	var errBody map[string]string
	_ = json.Unmarshal(raw, &errBody)
	if errBody["detail"] != sessionBusyDetail {
		t.Fatalf("409 detail = %q", errBody["detail"])
	}

	close(hold)
	<-done
}

func TestSendMessageHappyPathAndStickyBind(t *testing.T) {
	runner := &stubRunner{tokens: []string{"hel", "@@tool:start", "lo"}}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
		bytes.NewBufferString(`{"message":" hi ","provider":"xai","model":"grok-4-latest","plan_mode":true}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != 200 {
		t.Fatalf("send = %d %s", resp.StatusCode, raw)
	}
	var body map[string]any
	_ = json.Unmarshal(raw, &body)
	if body["response"] != "hello" {
		t.Fatalf("response = %#v (@@ filtered?)", body["response"])
	}
	if body["session_id"] != sid {
		t.Fatalf("session_id = %#v", body["session_id"])
	}
	if _, ok := body["request_id"].(string); !ok || body["request_id"] == "" {
		t.Fatalf("request_id = %#v", body["request_id"])
	}
	if _, ok := body["processing_time_ms"].(float64); !ok {
		t.Fatalf("processing_time_ms = %#v", body["processing_time_ms"])
	}
	call, ok := runner.lastCall()
	if !ok || call.Prompt != "hi" || !call.PlanMode {
		t.Fatalf("runner call = %#v", call)
	}
	if call.Provider == nil || *call.Provider != "xai" || call.Model == nil || *call.Model != "grok-4-latest" {
		t.Fatalf("bind = %#v %#v", call.Provider, call.Model)
	}

	// Sticky: lone foreign model must not steal.
	runner2tokens := &stubRunner{tokens: []string{"pong"}}
	// Reuse same server — swap runner via new server on same db is hard; second send on same server.
	req = authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
		bytes.NewBufferString(`{"message":"next","model":"deepseek-chat"}`))
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	_ = runner2tokens
	call, _ = runner.lastCall()
	if call.Provider == nil || *call.Provider != "xai" || call.Model == nil || *call.Model != "grok-4-latest" {
		t.Fatalf("sticky bind broken: %#v %#v", call.Provider, call.Model)
	}
}

func TestSendMessageProcessedFallback(t *testing.T) {
	runner := &stubRunner{tokens: nil}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
		bytes.NewBufferString(`{"message":"hi"}`))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	var body map[string]any
	_ = json.Unmarshal(raw, &body)
	if body["response"] != "Processed." {
		t.Fatalf("response = %#v", body["response"])
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
	if len(listed.Messages) != 1 || listed.Messages[0].Role != "user" {
		t.Fatalf("silent model should store user only: %#v", listed.Messages)
	}
}

func TestSendMessageAttachmentJail(t *testing.T) {
	home := t.TempDir()
	runner := &stubRunner{tokens: []string{"ok"}}
	base, shutdown, token := startMessagesServer(t, runner, home)
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	secret := filepath.Join(home, "secret.txt")
	if err := os.WriteFile(secret, []byte("password hunter2"), 0o600); err != nil {
		t.Fatal(err)
	}
	payload, _ := json.Marshal(map[string]any{
		"message": "read this",
		"attachments": []map[string]any{
			{"path": secret, "name": "secret.txt", "mime": "text/plain"},
		},
	})
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token, bytes.NewReader(payload))
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	call, _ := runner.lastCall()
	if len(call.Attachments) != 0 {
		t.Fatalf("jailed path leaked: %#v", call.Attachments)
	}

	// Allowed upload under session attachments dir.
	attDir := filepath.Join(home, "attachments", sid)
	if err := os.MkdirAll(attDir, 0o700); err != nil {
		t.Fatal(err)
	}
	note := filepath.Join(attDir, "note.txt")
	if err := os.WriteFile(note, []byte("the file body"), 0o600); err != nil {
		t.Fatal(err)
	}
	payload, _ = json.Marshal(map[string]any{
		"message": "look",
		"attachments": []map[string]any{
			{"path": note, "name": "note.txt", "mime": "text/plain", "size": 13},
		},
	})
	req = authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token, bytes.NewReader(payload))
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	resp.Body.Close()
	call, _ = runner.lastCall()
	if len(call.Attachments) != 1 || call.Prompt != "look" {
		t.Fatalf("allowed att: %#v", call)
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
	userContent, _ := listed.Messages[len(listed.Messages)-2].Content.(string)
	if !strings.Contains(userContent, "Attached files") || !strings.Contains(userContent, "the file body") {
		t.Fatalf("stored user content = %q", userContent)
	}
}

func TestAbortSessionEpoch(t *testing.T) {
	hold := make(chan struct{})
	runner := &stubRunner{tokens: []string{"x"}, hold: hold}
	base, shutdown, token := startMessagesServer(t, runner, t.TempDir())
	defer shutdown()
	client := &http.Client{Timeout: 5 * time.Second}
	sid := createSessionID(t, client, base, token)

	go func() {
		req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/messages", token,
			bytes.NewBufferString(`{"message":"a"}`))
		resp, err := client.Do(req)
		if err == nil {
			resp.Body.Close()
		}
	}()

	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		req := authReq(t, http.MethodGet, base+"/api/turn-active", token, nil)
		resp, _ := client.Do(req)
		raw, _ := io.ReadAll(resp.Body)
		resp.Body.Close()
		var body map[string]any
		_ = json.Unmarshal(raw, &body)
		if body["active"] == true {
			break
		}
		time.Sleep(20 * time.Millisecond)
	}

	// Stale epoch → ignored
	req := authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/abort?reason=stop&epoch=999", token, nil)
	resp, err := client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	var stale map[string]any
	_ = json.Unmarshal(raw, &stale)
	if stale["status"] != "ignored" || stale["notified"].(float64) != 0 {
		t.Fatalf("stale abort = %#v", stale)
	}

	// Abort without epoch → aborted
	req = authReq(t, http.MethodPost, base+"/api/sessions/"+sid+"/abort?reason=stop", token, nil)
	resp, err = client.Do(req)
	if err != nil {
		t.Fatal(err)
	}
	raw, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	var live map[string]any
	_ = json.Unmarshal(raw, &live)
	if live["status"] != "aborted" || live["notified"].(float64) != 1 || live["reason"] != "stop" {
		t.Fatalf("live abort = %#v", live)
	}
	close(hold)
}

func TestStreamClaimUnit(t *testing.T) {
	c := newStreamClaims()
	if !c.TryClaim("s1") {
		t.Fatal("first claim")
	}
	if c.TryClaim("s1") {
		t.Fatal("second claim should fail")
	}
	ep := c.Epoch("s1")
	if ep != 1 {
		t.Fatalf("epoch = %d", ep)
	}
	n := c.Abort("s1", &ep, strPtr("supersede"))
	if n != 1 || c.PeekAbortReason("s1") != abortReasonSupersede {
		t.Fatalf("abort n=%d reason=%q", n, c.PeekAbortReason("s1"))
	}
	if !c.IsClaimed("s1") {
		t.Fatal("claim must remain until release")
	}
	c.Release("s1", &ep)
	if c.IsClaimed("s1") {
		t.Fatal("released")
	}
	if !c.TryClaim("s1") {
		t.Fatal("reclaim after release")
	}
	if c.Epoch("s1") != 2 {
		t.Fatalf("epoch bump = %d", c.Epoch("s1"))
	}
}

func strPtr(s string) *string { return &s }
