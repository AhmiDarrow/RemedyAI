package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func newToolsAPIServer(t *testing.T) *Server {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	runner := NewCognitionTurnRunner(&cognition.ScriptedModel{})
	s, err := New(Config{
		HomeDir:    home,
		Token:      "tok-tools-api-test",
		DBPath:     filepath.Join(home, "memory.db"),
		TurnRunner: runner,
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func doTools(t *testing.T, s *Server, method, path, body string) (int, []byte) {
	t.Helper()
	var r *http.Request
	if body != "" {
		r = httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
	} else {
		r = httptest.NewRequest(method, path, nil)
	}
	r.Header.Set("Authorization", "Bearer tok-tools-api-test")
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	return rr.Code, rr.Body.Bytes()
}

func TestListToolsIncludesGoBuiltins(t *testing.T) {
	s := newToolsAPIServer(t)
	code, raw := doTools(t, s, http.MethodGet, "/api/tools", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	tools, _ := out["tools"].([]any)
	if len(tools) < 1 {
		t.Fatalf("expected builtins, got %v", out)
	}
	found := false
	for _, item := range tools {
		m, _ := item.(map[string]any)
		if m["id"] == "runtime.probe" {
			found = true
			break
		}
	}
	if !found {
		t.Fatalf("runtime.probe missing: %v", out)
	}
}

func TestListToolsSearchFilters(t *testing.T) {
	s := newToolsAPIServer(t)
	code, raw := doTools(t, s, http.MethodGet, "/api/tools?q=runtime.probe", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if int(out["count"].(float64)) != 1 {
		t.Fatalf("expected 1 match, got %v", out)
	}
}

func TestInvokeRuntimeProbe(t *testing.T) {
	s := newToolsAPIServer(t)
	code, raw := doTools(t, s, http.MethodPost, "/api/tools/invoke", `{"id":"runtime.probe","input":{}}`)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["ok"] != true {
		t.Fatalf("expected ok: %v", out)
	}
	output, _ := out["output"].(map[string]any)
	if output["status"] != "ready" {
		t.Fatalf("probe output=%v", output)
	}
}

func TestInvokeUnknownTool404(t *testing.T) {
	s := newToolsAPIServer(t)
	code, raw := doTools(t, s, http.MethodPost, "/api/tools/invoke", `{"id":"no.such.tool","input":{}}`)
	if code != http.StatusNotFound {
		t.Fatalf("status=%d body=%s", code, raw)
	}
}

func TestToolsUnavailableWithoutRegistry(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	s, err := New(Config{
		HomeDir: home,
		Token:   "tok-tools-api-test",
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	code, _ := doTools(t, s, http.MethodGet, "/api/tools", "")
	if code != http.StatusServiceUnavailable {
		t.Fatalf("status=%d want 503", code)
	}
}

func TestInvokeReadOnlyStillAllowedInAskMode(t *testing.T) {
	s := newToolsAPIServer(t)
	_ = s.approvals.SetMode("ask")
	code, raw := doTools(t, s, http.MethodPost, "/api/tools/invoke", `{"id":"runtime.probe","input":{}}`)
	if code != http.StatusOK {
		t.Fatalf("read-only status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["ok"] != true {
		t.Fatalf("expected ok: %v", out)
	}
}

func TestInvokeMutationAskBlocks(t *testing.T) {
	s := newToolsAPIServer(t)
	_ = s.approvals.SetMode("ask")
	body := `{"id":"shell.exec","session_id":"sess-ask","input":{"argv":["C:\\Windows\\System32\\cmd.exe","/c","echo"]}}`
	code, raw := doTools(t, s, http.MethodPost, "/api/tools/invoke", body)
	if code != http.StatusForbidden {
		t.Fatalf("mutation Ask status=%d body=%s want 403", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["ok"] != false || out["approval_required"] != true {
		t.Fatalf("expected approval_required block: %v", out)
	}
	if out["error"] != "approval required" {
		t.Fatalf("error=%v", out["error"])
	}
	pending := s.approvals.ListPending("sess-ask")
	if len(pending) != 1 || pending[0].ToolName != "shell.exec" {
		t.Fatalf("pending=%v", pending)
	}

	// computer.click must also Ask-block (no silent run).
	code2, raw2 := doTools(t, s, http.MethodPost, "/api/tools/invoke",
		`{"id":"computer.click","session_id":"sess-ask","input":{"x":10,"y":20}}`)
	if code2 != http.StatusForbidden {
		t.Fatalf("computer.click Ask status=%d body=%s want 403", code2, raw2)
	}
}

func TestInvokeMutationApprovedFingerprintAllows(t *testing.T) {
	s := newToolsAPIServer(t)
	_ = s.approvals.SetMode("ask")
	sid := "sess-fp"
	body := `{"id":"shell.exec","session_id":"sess-fp","input":{"argv":["C:\\Windows\\System32\\cmd.exe","/c","echo"]}}`

	// First call asks. The owner approves that pending item, which is
	// fingerprinted on the bound call — the command that will actually run.
	if first, firstRaw := doTools(t, s, http.MethodPost, "/api/tools/invoke", body); first != http.StatusForbidden {
		t.Fatalf("first invoke status=%d body=%s want 403 (Ask)", first, firstRaw)
	}
	pending := s.approvals.ListPending(sid)
	if len(pending) != 1 {
		t.Fatalf("pending=%d want 1", len(pending))
	}
	if resolved := s.approvals.Resolve(pending[0].ID, true, "session"); resolved == nil {
		t.Fatalf("approval %s could not be resolved", pending[0].ID)
	}

	// The same call must now pass the gate without asking again.
	code, raw := doTools(t, s, http.MethodPost, "/api/tools/invoke", body)
	if code == http.StatusForbidden {
		var out map[string]any
		_ = json.Unmarshal(raw, &out)
		if out["approval_required"] == true {
			t.Fatalf("approved fingerprint still Ask-blocked: %s", raw)
		}
	}
	// Past policy: execute may fail closed without Zig core (not an Ask 403).
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["approval_required"] == true {
		t.Fatalf("should not require approval after fingerprint: %v", out)
	}
}

func TestInvokeMutationAutoAllowsPastPolicy(t *testing.T) {
	s := newToolsAPIServer(t)
	_ = s.approvals.SetMode("auto")
	body := `{"id":"computer.click","input":{"x":1,"y":2}}`
	code, raw := doTools(t, s, http.MethodPost, "/api/tools/invoke", body)
	if code == http.StatusForbidden {
		var out map[string]any
		_ = json.Unmarshal(raw, &out)
		if out["approval_required"] == true {
			t.Fatalf("auto mode must not Ask-block mutations: %s", raw)
		}
	}
}
