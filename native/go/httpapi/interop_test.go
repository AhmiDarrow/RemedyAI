package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
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
}
