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

const xaiTestToken = "tok-xai-test-not-a-secret"

func newXaiTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("XAI_API_KEY", "")
	t.Setenv("REMEDY_XAI_API_KEY", "")
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   xaiTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doXaiJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+xaiTestToken)
	req.Header.Set("Content-Type", "application/json")
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload, rr.Body.String()
}

func TestXaiAuthStatusDisconnected(t *testing.T) {
	s, _ := newXaiTestServer(t)
	code, body, text := doXaiJSON(t, s, http.MethodGet, "/api/auth/xai", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["provider"] != "xai" || body["connected"] != false {
		t.Fatalf("body=%v", body)
	}
	if body["auth_method"] != "none" {
		t.Fatalf("auth_method=%v", body["auth_method"])
	}
}

func TestXaiAuthAPIKeyAndLogout(t *testing.T) {
	s, home := newXaiTestServer(t)
	code, body, text := doXaiJSON(t, s, http.MethodPost, "/api/auth/xai/apikey", map[string]any{
		"api_key": "xai-test-console-key-not-real",
	})
	if code != http.StatusOK {
		t.Fatalf("apikey %d %s", code, text)
	}
	if body["status"] != "saved" || body["connected"] != true || body["auth_method"] != "api_key" {
		t.Fatalf("apikey body=%v", body)
	}
	if _, err := os.Stat(filepath.Join(home, "auth", "xai.json")); err != nil {
		t.Fatalf("xai.json missing: %v", err)
	}
	raw, _ := os.ReadFile(filepath.Join(home, "auth", "xai.json"))
	if strings.Contains(string(raw), "xai-test-console-key-not-real") && sealedAuthEncoding(filepath.Join(home, "auth", "xai.json")) == "dpapi" {
		t.Fatal("dpapi envelope should not contain plaintext key")
	}

	code, body, text = doXaiJSON(t, s, http.MethodGet, "/api/auth/xai", nil)
	if code != http.StatusOK || body["connected"] != true {
		t.Fatalf("status after save %d %s", code, text)
	}

	code, body, text = doXaiJSON(t, s, http.MethodPost, "/api/auth/xai/apikey", map[string]any{
		"api_key": "   ",
	})
	if code != http.StatusBadRequest {
		t.Fatalf("empty key want 400 got %d %s", code, text)
	}

	code, body, text = doXaiJSON(t, s, http.MethodDelete, "/api/auth/xai", nil)
	if code != http.StatusOK || body["status"] != "logged_out" || body["connected"] != false {
		t.Fatalf("logout %d %s", code, text)
	}
	if _, err := os.Stat(filepath.Join(home, "auth", "xai.json")); !os.IsNotExist(err) {
		t.Fatalf("xai.json should be gone: %v", err)
	}
}

func TestXaiOAuthMeta(t *testing.T) {
	s, _ := newXaiTestServer(t)
	code, body, text := doXaiJSON(t, s, http.MethodGet, "/api/auth/xai/oauth-meta", nil)
	if code != http.StatusOK {
		t.Fatalf("meta %d %s", code, text)
	}
	if body["oauth_build"] != xaiOAuthBuildID {
		t.Fatalf("oauth_build=%v", body["oauth_build"])
	}
	url, _ := body["device_code_url"].(string)
	if !strings.Contains(url, "auth.x.ai") {
		t.Fatalf("device_code_url=%v", body["device_code_url"])
	}
}

func TestXaiLoginStatusUnknownSession(t *testing.T) {
	s, _ := newXaiTestServer(t)
	code, body, text := doXaiJSON(t, s, http.MethodGet, "/api/auth/xai/login/status?session_id=NOPE", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d %s", code, text)
	}
	sess, _ := body["session"].(map[string]any)
	if sess["status"] != "unknown" {
		t.Fatalf("session=%v", sess)
	}
}
