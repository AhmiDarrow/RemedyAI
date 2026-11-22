package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"
)

const interopTestToken = "tok-interop-test-token-value-32b"

func newInteropServer(t *testing.T, inbound bool) *Server {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_DEV_ROOT", "")
	t.Setenv("REMEDY_INTEROP_TOKEN", interopTestToken)
	t.Chdir(t.TempDir())
	if inbound {
		cfg := []byte("[advanced]\ninterop_inbound = true\n")
		if err := os.WriteFile(filepath.Join(home, "config.toml"), cfg, 0o600); err != nil {
			t.Fatal(err)
		}
	}
	s, err := New(Config{
		HomeDir: home,
		Token:   "tok-desktop-api",
		DBPath:  filepath.Join(home, "memory.db"),
		Version: "0.70.5-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func doInterop(t *testing.T, s *Server, method, path, body, token string, extra http.Header) (int, []byte, http.Header) {
	t.Helper()
	var r *http.Request
	if body != "" {
		r = httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
	} else {
		r = httptest.NewRequest(method, path, nil)
	}
	r.Header.Set("Accept", "application/json")
	if token != "" {
		r.Header.Set("Authorization", "Bearer "+token)
	}
	for k, vs := range extra {
		for _, v := range vs {
			r.Header.Set(k, v)
		}
	}
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	return rr.Code, rr.Body.Bytes(), rr.Header()
}

func TestInteropDisabledReturns404(t *testing.T) {
	s := newInteropServer(t, false)
	code, _, _ := doInterop(t, s, http.MethodGet, "/.well-known/agent-card.json", "", interopTestToken, nil)
	if code != http.StatusNotFound {
		t.Fatalf("disabled card status=%d", code)
	}
	code, _, _ = doInterop(t, s, http.MethodPost, "/a2a", `{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{}}`, interopTestToken, nil)
	if code != http.StatusNotFound {
		t.Fatalf("disabled a2a status=%d", code)
	}
}

func TestInteropAgentCardAndSendMessage(t *testing.T) {
	s := newInteropServer(t, true)
	code, raw, _ := doInterop(t, s, http.MethodGet, "/.well-known/agent-card.json", "", "", nil)
	if code != http.StatusOK {
		t.Fatalf("card status=%d body=%s", code, raw)
	}
	var card map[string]any
	if err := json.Unmarshal(raw, &card); err != nil {
		t.Fatal(err)
	}
	if card["name"] != "Remedy" {
		t.Fatalf("name=%v", card["name"])
	}
	ifaces, _ := card["supportedInterfaces"].([]any)
	if len(ifaces) == 0 {
		t.Fatal("missing supportedInterfaces")
	}
	iface, _ := ifaces[0].(map[string]any)
	if iface["protocolBinding"] != "JSONRPC" || iface["protocolVersion"] != "1.0" {
		t.Fatalf("interface=%v", iface)
	}

	// POST without token is 401
	code, _, _ = doInterop(t, s, http.MethodPost, "/a2a", `{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"parts":[{"text":"hello"}]}}}`, "", nil)
	if code != http.StatusUnauthorized {
		t.Fatalf("unauth status=%d", code)
	}

	body := `{"jsonrpc":"2.0","id":7,"method":"SendMessage","params":{"message":{"role":"user","parts":[{"text":"hello from a peer"}]}}}`
	code, raw, _ = doInterop(t, s, http.MethodPost, "/a2a", body, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("send status=%d body=%s", code, raw)
	}
	var resp map[string]any
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatal(err)
	}
	result, _ := resp["result"].(map[string]any)
	task, _ := result["task"].(map[string]any)
	if task == nil {
		t.Fatalf("no task in %s", raw)
	}
	if task["id"] == nil {
		t.Fatalf("task missing id: %s", raw)
	}
	st, _ := task["status"].(map[string]any)
	if st["state"] != "TASK_STATE_SUBMITTED" && st["state"] != "TASK_STATE_REJECTED" && st["state"] != "TASK_STATE_INPUT_REQUIRED" {
		t.Fatalf("state=%v", st["state"])
	}

	// v0.3 alias + GetTask
	tid, _ := task["id"].(string)
	get := `{"jsonrpc":"2.0","id":8,"method":"tasks/get","params":{"id":"` + tid + `"}}`
	code, raw, _ = doInterop(t, s, http.MethodPost, "/a2a", get, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("get status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatal(err)
	}
	got, _ := resp["result"].(map[string]any)
	if got["id"] != tid {
		t.Fatalf("get task=%v", got)
	}
}

func TestInteropMCPTalkTools(t *testing.T) {
	s := newInteropServer(t, true)
	initBody := `{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2026-07-28","capabilities":{},"clientInfo":{"name":"test"}}}`
	code, raw, _ := doInterop(t, s, http.MethodPost, "/mcp", initBody, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("init status=%d body=%s", code, raw)
	}
	list := `{"jsonrpc":"2.0","id":2,"method":"tools/list","params":{}}`
	code, raw, _ = doInterop(t, s, http.MethodPost, "/mcp", list, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("list status=%d body=%s", code, raw)
	}
	var resp map[string]any
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatal(err)
	}
	tools, _ := resp["result"].(map[string]any)["tools"].([]any)
	names := map[string]bool{}
	for _, item := range tools {
		m, _ := item.(map[string]any)
		names[m["name"].(string)] = true
	}
	for _, want := range []string{"remedy_ask", "remedy_delegate", "remedy_task_status", "remedy_share_context"} {
		if !names[want] {
			t.Fatalf("missing tool %s in %v", want, names)
		}
	}
	ask := `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"remedy_ask","arguments":{"question":"What is on the list?"}}}`
	code, raw, _ = doInterop(t, s, http.MethodPost, "/mcp", ask, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("ask status=%d body=%s", code, raw)
	}
	if !strings.Contains(string(raw), "task_id") {
		t.Fatalf("ask body=%s", raw)
	}
}

func TestInteropKnobsPresent(t *testing.T) {
	tbl := advancedTable(ConfigMap{})
	if tbl["interop_inbound"] != false {
		t.Fatalf("inbound default=%v", tbl["interop_inbound"])
	}
	if tbl["interop_trust_policy"] != "gated" {
		t.Fatalf("policy default=%v", tbl["interop_trust_policy"])
	}
	if tbl["interop_bind"] != "127.0.0.1" {
		t.Fatalf("bind default=%v", tbl["interop_bind"])
	}
	if tbl["interop_auto_reply"] != true {
		t.Fatalf("auto_reply default=%v", tbl["interop_auto_reply"])
	}
	if tbl["interop_max_concurrent"] != 2 {
		t.Fatalf("max_concurrent default=%v", tbl["interop_max_concurrent"])
	}
}

func writeInteropPeer(t *testing.T, home, id string, perms []string) {
	t.Helper()
	dir := filepath.Join(home, "interop")
	if err := os.MkdirAll(dir, 0o700); err != nil {
		t.Fatal(err)
	}
	arr := make([]any, 0, len(perms))
	for _, p := range perms {
		arr = append(arr, p)
	}
	payload, _ := json.Marshal(map[string]any{
		"version": 1,
		"peers": map[string]any{
			id: map[string]any{"id": id, "protocol": "a2a", "enabled": true, "permissions": arr},
		},
	})
	if err := os.WriteFile(filepath.Join(dir, "peers.json"), payload, 0o600); err != nil {
		t.Fatal(err)
	}
}

func newInteropLiveServer(t *testing.T, runner TurnRunner, extraCfg string) *Server {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_DEV_ROOT", "")
	t.Setenv("REMEDY_INTEROP_TOKEN", interopTestToken)
	t.Chdir(t.TempDir())
	cfg := "[advanced]\ninterop_inbound = true\ninterop_trust_policy = \"open\"\n" + extraCfg
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	writeInteropPeer(t, home, "claude", []string{"ask", "delegate", "share_context", "task_status", "spend", "send", "delete"})
	s, err := New(Config{
		HomeDir:    home,
		Token:      "tok-desktop-api",
		DBPath:     filepath.Join(home, "memory.db"),
		Version:    "0.70.5-test",
		TurnRunner: runner,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func TestInteropConsentUnknownPeerIsQueued(t *testing.T) {
	s := newInteropServer(t, true)
	body := `{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"role":"user","parts":[{"text":"hello from a stranger"}]}}}`
	code, raw, _ := doInterop(t, s, http.MethodPost, "/a2a", body, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var resp map[string]any
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatal(err)
	}
	task, _ := resp["result"].(map[string]any)["task"].(map[string]any)
	st, _ := task["status"].(map[string]any)
	if st["state"] != "TASK_STATE_INPUT_REQUIRED" {
		t.Fatalf("unknown peer should queue, state=%v body=%s", st["state"], raw)
	}
}

func TestInteropLiveSendMessageAnswers(t *testing.T) {
	runner := &stubRunner{tokens: []string{"Hello ", "from Remedy"}}
	s := newInteropLiveServer(t, runner, "")
	body := `{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"role":"user","parts":[{"text":"What is on the list?"}],"metadata":{"peer_id":"claude","action":"ask"}}}}`
	code, raw, _ := doInterop(t, s, http.MethodPost, "/a2a", body, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var resp map[string]any
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatal(err)
	}
	task, _ := resp["result"].(map[string]any)["task"].(map[string]any)
	st, _ := task["status"].(map[string]any)
	if st["state"] != "TASK_STATE_COMPLETED" {
		t.Fatalf("state=%v body=%s", st["state"], raw)
	}
	msg, _ := st["message"].(map[string]any)
	if extractA2AText(msg) != "Hello from Remedy" {
		t.Fatalf("reply=%q body=%s", extractA2AText(msg), raw)
	}
	req, ok := runner.lastCall()
	if !ok {
		t.Fatal("runner was not called")
	}
	if !strings.HasPrefix(req.Origin, "interop:a2a:claude") {
		t.Fatalf("origin=%q want untrusted interop origin", req.Origin)
	}
	if OriginIsOwner(req.Origin) {
		t.Fatalf("origin %q must not be treated as the owner", req.Origin)
	}
	// audit
	audit, err := os.ReadFile(filepath.Join(s.homeDir, "interop", "audit.jsonl"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(audit), "Hello from Remedy") {
		t.Fatalf("audit missing live reply: %s", audit)
	}
}

func TestInteropLiveSpendIsQueuedNotRun(t *testing.T) {
	runner := &stubRunner{tokens: []string{"should not run"}}
	s := newInteropLiveServer(t, runner, "")
	body := `{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"role":"user","parts":[{"text":"pay the invoice"}],"metadata":{"peer_id":"claude","action":"spend"}}}}`
	code, raw, _ := doInterop(t, s, http.MethodPost, "/a2a", body, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var resp map[string]any
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatal(err)
	}
	task, _ := resp["result"].(map[string]any)["task"].(map[string]any)
	st, _ := task["status"].(map[string]any)
	if st["state"] != "TASK_STATE_INPUT_REQUIRED" {
		t.Fatalf("spend must queue, state=%v body=%s", st["state"], raw)
	}
	if _, ok := runner.lastCall(); ok {
		t.Fatal("spend must not start a live turn")
	}
}

func TestInteropLiveStreamingStatusAndArtifact(t *testing.T) {
	runner := &stubRunner{tokens: []string{"streamed ", "reply"}}
	s := newInteropLiveServer(t, runner, "")
	body := `{"jsonrpc":"2.0","id":9,"method":"SendStreamingMessage","params":{"message":{"role":"user","parts":[{"text":"stream please"}],"metadata":{"peer_id":"claude"}}}}`
	hdr := http.Header{}
	hdr.Set("Accept", "text/event-stream")
	var r *http.Request
	r = httptest.NewRequest(http.MethodPost, "/a2a", strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Accept", "text/event-stream")
	r.Header.Set("Authorization", "Bearer "+interopTestToken)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d body=%s", rr.Code, rr.Body.Bytes())
	}
	ct := rr.Header().Get("Content-Type")
	if !strings.Contains(ct, "text/event-stream") {
		t.Fatalf("content-type=%s", ct)
	}
	payload := rr.Body.String()
	if !strings.Contains(payload, "status-update") {
		t.Fatalf("missing status-update: %s", payload)
	}
	if !strings.Contains(payload, "artifact-update") {
		t.Fatalf("missing artifact-update: %s", payload)
	}
	if !strings.Contains(payload, "streamed reply") {
		t.Fatalf("missing streamed text: %s", payload)
	}
	if !strings.Contains(payload, "TASK_STATE_COMPLETED") {
		t.Fatalf("missing completed: %s", payload)
	}
	_ = hdr
}

func TestInteropLiveTimeout(t *testing.T) {
	hold := make(chan struct{})
	runner := &stubRunner{tokens: []string{"slow"}, hold: hold}
	s := newInteropLiveServer(t, runner, "")
	s.liveCtl().ttlOverride = 150 * time.Millisecond
	body := `{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"role":"user","parts":[{"text":"please timeout"}],"metadata":{"peer_id":"claude"}}}}`
	code, raw, _ := doInterop(t, s, http.MethodPost, "/a2a", body, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var resp map[string]any
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatal(err)
	}
	task, _ := resp["result"].(map[string]any)["task"].(map[string]any)
	st, _ := task["status"].(map[string]any)
	if st["state"] != "TASK_STATE_FAILED" {
		t.Fatalf("state=%v body=%s", st["state"], raw)
	}
	msg, _ := st["message"].(map[string]any)
	if !strings.Contains(extractA2AText(msg), "timed out") {
		t.Fatalf("expected timeout text, got %q", extractA2AText(msg))
	}
}

func TestInteropLiveConcurrencyBound(t *testing.T) {
	hold := make(chan struct{})
	runner := &stubRunner{tokens: []string{"ok"}, hold: hold}
	s := newInteropLiveServer(t, runner, "interop_max_concurrent = 1\n")
	s.liveCtl().ttlOverride = 3 * time.Second

	start := func(text string) []byte {
		body := `{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"role":"user","parts":[{"text":"` + text + `"}],"metadata":{"peer_id":"claude"}}}}`
		code, raw, _ := doInterop(t, s, http.MethodPost, "/a2a", body, interopTestToken, nil)
		if code != http.StatusOK {
			t.Fatalf("status=%d body=%s", code, raw)
		}
		return raw
	}

	var firstRaw, secondRaw []byte
	var wg sync.WaitGroup
	wg.Add(1)
	go func() {
		defer wg.Done()
		firstRaw = start("first unique prompt")
	}()
	deadline := time.Now().Add(2 * time.Second)
	for time.Now().Before(deadline) {
		runner.mu.Lock()
		n := len(runner.calls)
		runner.mu.Unlock()
		if n >= 1 {
			break
		}
		time.Sleep(10 * time.Millisecond)
	}
	runner.mu.Lock()
	if len(runner.calls) != 1 {
		runner.mu.Unlock()
		t.Fatalf("expected first turn to be in flight, calls=%d", len(runner.calls))
	}
	runner.mu.Unlock()

	wg.Add(1)
	secondStarted := make(chan struct{})
	go func() {
		defer wg.Done()
		close(secondStarted)
		secondRaw = start("second unique prompt")
	}()
	<-secondStarted
	time.Sleep(80 * time.Millisecond)
	runner.mu.Lock()
	n := len(runner.calls)
	runner.mu.Unlock()
	if n != 1 {
		t.Fatalf("second turn started while first still held, calls=%d", n)
	}
	close(hold)
	wg.Wait()
	for i, raw := range [][]byte{firstRaw, secondRaw} {
		var resp map[string]any
		if err := json.Unmarshal(raw, &resp); err != nil {
			t.Fatalf("raw %d: %v", i, err)
		}
		task, _ := resp["result"].(map[string]any)["task"].(map[string]any)
		st, _ := task["status"].(map[string]any)
		if st["state"] != "TASK_STATE_COMPLETED" {
			t.Fatalf("turn %d state=%v body=%s", i, st["state"], raw)
		}
	}
}

func TestInteropLiveMCPAskAnswers(t *testing.T) {
	runner := &stubRunner{tokens: []string{"MCP reply"}}
	s := newInteropLiveServer(t, runner, "")
	ask := `{"jsonrpc":"2.0","id":3,"method":"tools/call","params":{"name":"remedy_ask","arguments":{"question":"What is on the list?","peer_id":"claude"}}}`
	code, raw, _ := doInterop(t, s, http.MethodPost, "/mcp", ask, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	if !strings.Contains(string(raw), "MCP reply") {
		t.Fatalf("missing live mcp reply: %s", raw)
	}
	if !strings.Contains(string(raw), "TASK_STATE_COMPLETED") {
		t.Fatalf("expected completed: %s", raw)
	}
	req, ok := runner.lastCall()
	if !ok {
		t.Fatal("runner was not called")
	}
	if !strings.HasPrefix(req.Origin, "interop:mcp:claude") {
		t.Fatalf("origin=%q", req.Origin)
	}
}

func TestInteropAutoReplyOffQueuesEvenWithRunner(t *testing.T) {
	runner := &stubRunner{tokens: []string{"nope"}}
	s := newInteropLiveServer(t, runner, "interop_auto_reply = false\n")
	body := `{"jsonrpc":"2.0","id":1,"method":"SendMessage","params":{"message":{"role":"user","parts":[{"text":"hello allowed peer"}],"metadata":{"peer_id":"claude"}}}}`
	code, raw, _ := doInterop(t, s, http.MethodPost, "/a2a", body, interopTestToken, nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var resp map[string]any
	if err := json.Unmarshal(raw, &resp); err != nil {
		t.Fatal(err)
	}
	task, _ := resp["result"].(map[string]any)["task"].(map[string]any)
	st, _ := task["status"].(map[string]any)
	if st["state"] != "TASK_STATE_SUBMITTED" {
		t.Fatalf("state=%v body=%s", st["state"], raw)
	}
	if _, ok := runner.lastCall(); ok {
		t.Fatal("runner should not run when auto-reply is off")
	}
}

func TestInteropDecideMatchesPythonPolicy(t *testing.T) {
	peer := map[string]any{"id": "p", "enabled": true, "permissions": []any{"ask", "spend", "send", "delete", "delegate"}}
	for _, action := range []string{"spend", "send", "delete", "transact", "communicate"} {
		d := interopDecide(action, peer, "verified", "open")
		if d.Outcome != "need_approval" {
			t.Fatalf("%s: %s %s", action, d.Outcome, d.Reason)
		}
	}
	unknownAsk := interopDecide("ask", nil, "unverified", "gated")
	if unknownAsk.Outcome != "need_approval" {
		t.Fatalf("unknown gated ask: %s", unknownAsk.Outcome)
	}
	knownAsk := interopDecide("ask", peer, "unverified", "gated")
	if knownAsk.Outcome != "allow" {
		t.Fatalf("known gated ask: %s %s", knownAsk.Outcome, knownAsk.Reason)
	}
}
