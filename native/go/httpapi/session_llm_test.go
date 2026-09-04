package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const sessionLLMTestToken = "tok-session-llm-test-not-a-secret"

func newSessionLLMTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   sessionLLMTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doSessionLLMJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			t.Fatal(err)
		}
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+sessionLLMTestToken)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	text := rr.Body.String()
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload, text
}

func createTestSession(t *testing.T, s *Server) string {
	t.Helper()
	code, body, text := doSessionLLMJSON(t, s, http.MethodPost, "/api/sessions", map[string]any{
		"title":         "LLM bind",
		"model":         "gpt-4o-mini",
		"llm_provider":  "openai",
	})
	if code != http.StatusOK {
		t.Fatalf("create session status=%d body=%s", code, text)
	}
	id, _ := body["id"].(string)
	if id == "" {
		t.Fatalf("missing session id: %s", text)
	}
	return id
}

func TestSetSessionLLMHappyPathPUTAndPOST(t *testing.T) {
	s, _ := newSessionLLMTestServer(t)
	sid := createTestSession(t, s)

	code, body, text := doSessionLLMJSON(t, s, http.MethodPut, "/api/sessions/"+sid+"/llm", map[string]any{
		"provider": "xai",
		"model":    "grok-4",
	})
	if code != http.StatusOK {
		t.Fatalf("PUT status=%d body=%s", code, text)
	}
	if body["status"] != "ok" || body["session_id"] != sid {
		t.Fatalf("PUT body=%s", text)
	}
	if body["provider"] != "xai" || body["model"] != "grok-4" {
		t.Fatalf("provider/model=%v/%v", body["provider"], body["model"])
	}
	if body["make_default"] != false {
		t.Fatalf("make_default=%v", body["make_default"])
	}
	toast, _ := body["toast"].(string)
	if !strings.Contains(toast, "xai") || !strings.Contains(toast, "grok-4") {
		t.Fatalf("toast=%q", toast)
	}

	sess, ok, err := s.sessions.Get(sid)
	if err != nil || !ok {
		t.Fatalf("get session ok=%v err=%v", ok, err)
	}
	if sess.LLMProvider == nil || *sess.LLMProvider != "xai" {
		t.Fatalf("stored provider=%v", sess.LLMProvider)
	}
	if sess.Model == nil || *sess.Model != "grok-4" {
		t.Fatalf("stored model=%v", sess.Model)
	}

	code, body, text = doSessionLLMJSON(t, s, http.MethodPost, "/api/sessions/"+sid+"/llm", map[string]any{
		"provider": "deepseek",
		"model":    "deepseek-v4-flash",
	})
	if code != http.StatusOK {
		t.Fatalf("POST status=%d body=%s", code, text)
	}
	if body["provider"] != "deepseek" || body["model"] != "deepseek-v4-flash" {
		t.Fatalf("POST body=%s", text)
	}
	sess, ok, err = s.sessions.Get(sid)
	if err != nil || !ok {
		t.Fatalf("get after POST ok=%v err=%v", ok, err)
	}
	if sess.LLMProvider == nil || *sess.LLMProvider != "deepseek" {
		t.Fatalf("stored provider after POST=%v", sess.LLMProvider)
	}
}

func TestSetSessionLLMNotFound(t *testing.T) {
	s, _ := newSessionLLMTestServer(t)
	code, body, text := doSessionLLMJSON(t, s, http.MethodPut, "/api/sessions/missing-session/llm", map[string]any{
		"provider": "xai",
		"model":    "grok-4",
	})
	if code != http.StatusNotFound {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["detail"] != "Session not found" {
		t.Fatalf("detail=%v body=%s", body["detail"], text)
	}
}

func TestSetSessionLLMConflictWhenStreamClaimed(t *testing.T) {
	s, _ := newSessionLLMTestServer(t)
	sid := createTestSession(t, s)
	_, _, ok := s.claims.TryClaim(sid)
	if !ok {
		t.Fatal("TryClaim failed")
	}
	t.Cleanup(func() { s.claims.Release(sid, nil) })

	code, body, text := doSessionLLMJSON(t, s, http.MethodPut, "/api/sessions/"+sid+"/llm", map[string]any{
		"provider": "xai",
		"model":    "grok-4",
	})
	if code != http.StatusConflict {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["detail"] != sessionLLMBusyDetail {
		t.Fatalf("detail=%v body=%s", body["detail"], text)
	}

	// Other session streaming must not block this tab.
	other := createTestSession(t, s)
	s.claims.Release(sid, nil)
	_, _, ok = s.claims.TryClaim(other)
	if !ok {
		t.Fatal("TryClaim other failed")
	}
	t.Cleanup(func() { s.claims.Release(other, nil) })

	code, body, text = doSessionLLMJSON(t, s, http.MethodPut, "/api/sessions/"+sid+"/llm", map[string]any{
		"provider": "anthropic",
		"model":    "claude-sonnet-4-6",
	})
	if code != http.StatusOK {
		t.Fatalf("other-tab stream should not block: status=%d body=%s", code, text)
	}
	if body["provider"] != "anthropic" {
		t.Fatalf("body=%s", text)
	}
}

func TestSetSessionLLMMakeDefaultWritesSettings(t *testing.T) {
	s, home := newSessionLLMTestServer(t)
	sid := createTestSession(t, s)

	code, body, text := doSessionLLMJSON(t, s, http.MethodPut, "/api/sessions/"+sid+"/llm", map[string]any{
		"provider":     "xai",
		"model":        "grok-4",
		"make_default": true,
	})
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["make_default"] != true {
		t.Fatalf("make_default=%v", body["make_default"])
	}
	if body["provider"] != "xai" || body["base_url"] == nil || body["base_url"] == "" {
		t.Fatalf("body=%s", text)
	}

	cfgPath := filepath.Join(home, "config.toml")
	raw, err := os.ReadFile(cfgPath)
	if err != nil {
		t.Fatal(err)
	}
	cfgText := string(raw)
	if !strings.Contains(cfgText, `llm_provider = "xai"`) {
		t.Fatalf("config missing llm_provider: %s", cfgText)
	}
	if !strings.Contains(cfgText, `llm_model = "grok-4"`) {
		t.Fatalf("config missing llm_model: %s", cfgText)
	}
	if !strings.Contains(cfgText, "[last_model_by_provider]") {
		t.Fatalf("config missing last_model_by_provider: %s", cfgText)
	}
	if !strings.Contains(cfgText, `xai = "grok-4"`) {
		t.Fatalf("config missing last model entry: %s", cfgText)
	}

	InvalidateConfigCache()
	got := LoadConfig(home)
	if cfgString(got, "llm_provider", "") != "xai" {
		t.Fatalf("loaded provider=%q", cfgString(got, "llm_provider", ""))
	}
	if cfgString(got, "llm_model", "") != "grok-4" {
		t.Fatalf("loaded model=%q", cfgString(got, "llm_model", ""))
	}
}

func TestSetSessionLLMRejectsGarbageModel(t *testing.T) {
	s, _ := newSessionLLMTestServer(t)
	sid := createTestSession(t, s)
	code, body, text := doSessionLLMJSON(t, s, http.MethodPut, "/api/sessions/"+sid+"/llm", map[string]any{
		"provider": "openai",
		"model":    "not-a-real-model-zzz",
	})
	if code != http.StatusBadRequest {
		t.Fatalf("status=%d body=%s", code, text)
	}
	detail, _ := body["detail"].(string)
	if !strings.Contains(detail, "Unknown model") {
		t.Fatalf("detail=%q body=%s", detail, text)
	}
}
