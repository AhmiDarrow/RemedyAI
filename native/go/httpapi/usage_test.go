package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const usageTestToken = "tok-usage-test-not-a-secret"

func newUsageTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   usageTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doUsage(t *testing.T, s *Server, method, path string) (int, map[string]any, string) {
	t.Helper()
	req := httptest.NewRequest(method, path, nil)
	req.Header.Set("Authorization", "Bearer "+usageTestToken)
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	raw := rr.Body.Bytes()
	var payload map[string]any
	_ = json.Unmarshal(raw, &payload)
	return rr.Code, payload, string(raw)
}

func TestUsageSummarySeriesExport(t *testing.T) {
	s, home := newUsageTestServer(t)
	now := float64(time.Now().Unix())
	if err := seedUsageEvent(home, now-60, "sess-a", "openai", "gpt-4o", 10, 20, 30, 0.001); err != nil {
		t.Fatal(err)
	}
	if err := seedUsageEvent(home, now-30, "sess-a", "anthropic", "claude", 5, 5, 10, 0.002); err != nil {
		t.Fatal(err)
	}
	if err := seedUsageEvent(home, now-10, "sess-b", "openai", "gpt-4o", 1, 1, 2, 0.0001); err != nil {
		t.Fatal(err)
	}

	code, body, text := doUsage(t, s, http.MethodGet, "/api/usage/summary?range_days=7")
	if code != http.StatusOK {
		t.Fatalf("summary status=%d body=%s", code, text)
	}
	totals, _ := body["totals"].(map[string]any)
	if int(totals["events"].(float64)) != 3 {
		t.Fatalf("events=%v body=%s", totals["events"], text)
	}
	if int(totals["total_tokens"].(float64)) != 42 {
		t.Fatalf("total_tokens=%v", totals["total_tokens"])
	}
	byProv, _ := body["by_provider"].([]any)
	if len(byProv) < 2 {
		t.Fatalf("by_provider=%v", byProv)
	}

	code, body, text = doUsage(t, s, http.MethodGet, "/api/usage/summary?range_days=7&session_id=sess-a")
	if code != http.StatusOK {
		t.Fatalf("session summary status=%d body=%s", code, text)
	}
	totals, _ = body["totals"].(map[string]any)
	if int(totals["events"].(float64)) != 2 {
		t.Fatalf("sess-a events=%v", totals["events"])
	}

	code, body, text = doUsage(t, s, http.MethodGet, "/api/usage/series?range_days=7&group=provider")
	if code != http.StatusOK {
		t.Fatalf("series status=%d body=%s", code, text)
	}
	if body["group"] != "provider" {
		t.Fatalf("group=%v", body["group"])
	}
	points, _ := body["points"].([]any)
	if len(points) == 0 {
		t.Fatalf("expected series points, got %s", text)
	}

	req := httptest.NewRequest(http.MethodGet, "/api/usage/export?range_days=7&format=csv", nil)
	req.Header.Set("Authorization", "Bearer "+usageTestToken)
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("export status=%d body=%s", rr.Code, rr.Body.String())
	}
	ct := rr.Header().Get("Content-Type")
	if !strings.Contains(ct, "text/csv") {
		t.Fatalf("content-type=%q", ct)
	}
	csv := rr.Body.String()
	if !strings.HasPrefix(csv, "day,provider,total_tokens,estimated_cost_usd,events\n") {
		t.Fatalf("csv header missing: %q", csv)
	}
	if !strings.Contains(csv, "openai") {
		t.Fatalf("csv missing openai: %q", csv)
	}

	code, body, text = doUsage(t, s, http.MethodGet, "/api/usage/export?range_days=7&format=json")
	if code != http.StatusOK {
		t.Fatalf("export json status=%d body=%s", code, text)
	}
	if body["summary"] == nil || body["series"] == nil {
		t.Fatalf("json export shape: %s", text)
	}
}

func TestUsageSummaryEmpty(t *testing.T) {
	s, _ := newUsageTestServer(t)
	code, body, text := doUsage(t, s, http.MethodGet, "/api/usage/summary")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	totals, _ := body["totals"].(map[string]any)
	if int(totals["events"].(float64)) != 0 {
		t.Fatalf("expected empty ledger, got %s", text)
	}
}
