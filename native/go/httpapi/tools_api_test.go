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
