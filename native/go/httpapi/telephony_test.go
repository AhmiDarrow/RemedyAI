package httpapi

import (
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"testing"
)

const telephonyTestToken = "tok-telephony-test-not-a-secret"

func newTelephonyTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   telephonyTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doTelephonyJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+telephonyTestToken)
	req.Header.Set("Content-Type", "application/json")
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	var payload map[string]any
	_ = json.Unmarshal(rr.Body.Bytes(), &payload)
	return rr.Code, payload, rr.Body.String()
}

func TestTelephonyStatusShape(t *testing.T) {
	s, _ := newTelephonyTestServer(t)
	code, body, text := doTelephonyJSON(t, s, http.MethodGet, "/api/telephony/status", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	terms, _ := body["terms"].(map[string]any)
	if terms == nil {
		t.Fatalf("missing terms: %s", text)
	}
	if terms["agreed"] != false {
		t.Fatalf("agreed=%v", terms["agreed"])
	}
	if int(terms["current_version"].(float64)) != telephonyTermsVersion {
		t.Fatalf("current_version=%v", terms["current_version"])
	}
	ask, _ := terms["ask"].(string)
	if ask == "" {
		t.Fatal("expected ask text before consent")
	}
	lines, ok := body["lines"].([]any)
	if !ok || len(lines) < 3 {
		t.Fatalf("lines=%v", body["lines"])
	}
	if body["phase"] != float64(0) || body["real_line"] != false {
		t.Fatalf("phase/real_line=%v/%v", body["phase"], body["real_line"])
	}
	if body["loopback"] != true {
		t.Fatalf("loopback=%v", body["loopback"])
	}
	offer, _ := body["offer"].(string)
	if offer == "" {
		t.Fatal("expected offer text")
	}
}

func TestTelephonyTermsAcceptAndWithdraw(t *testing.T) {
	s, home := newTelephonyTestServer(t)
	code, body, text := doTelephonyJSON(t, s, http.MethodPost, "/api/telephony/terms", map[string]any{
		"accept": true,
	})
	if code != http.StatusOK || body["ok"] != true || body["agreed"] != true {
		t.Fatalf("accept %d %s", code, text)
	}
	if _, err := os.Stat(filepath.Join(home, "telephony", "consent.json")); err != nil {
		t.Fatalf("consent file missing: %v", err)
	}
	code, status, text := doTelephonyJSON(t, s, http.MethodGet, "/api/telephony/status", nil)
	if code != http.StatusOK {
		t.Fatalf("status %d %s", code, text)
	}
	terms, _ := status["terms"].(map[string]any)
	if terms["agreed"] != true {
		t.Fatalf("terms after accept=%v", terms)
	}
	if terms["ask"] != "" {
		t.Fatalf("ask should be empty after accept: %v", terms["ask"])
	}

	code, body, text = doTelephonyJSON(t, s, http.MethodPost, "/api/telephony/terms", map[string]any{
		"accept": false,
	})
	if code != http.StatusOK || body["ok"] != true || body["agreed"] != false {
		t.Fatalf("withdraw %d %s", code, text)
	}
}

func TestTelephonyChooseLine(t *testing.T) {
	s, home := newTelephonyTestServer(t)
	code, body, text := doTelephonyJSON(t, s, http.MethodPost, "/api/telephony/choose", map[string]any{
		"name": "sip",
	})
	if code != http.StatusOK || body["ok"] != true || body["chosen"] != "sip" {
		t.Fatalf("choose %d %s", code, text)
	}
	raw, err := os.ReadFile(filepath.Join(home, "telephony", "line.json"))
	if err != nil {
		t.Fatal(err)
	}
	var parsed map[string]any
	_ = json.Unmarshal(raw, &parsed)
	if parsed["line"] != "sip" {
		t.Fatalf("line.json=%v", parsed)
	}

	code, body, text = doTelephonyJSON(t, s, http.MethodPost, "/api/telephony/choose", map[string]any{
		"name": "not-a-line",
	})
	if code != http.StatusOK || body["ok"] != false {
		t.Fatalf("bad choose %d %s", code, text)
	}
	if body["error"] == nil {
		t.Fatalf("expected error: %s", text)
	}
}
