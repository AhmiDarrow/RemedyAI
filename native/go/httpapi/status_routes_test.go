package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

const statusRoutesToken = "tok-status-routes-test"

func newStatusRoutesServer(t *testing.T) *Server {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_DEV_ROOT", "")
	t.Setenv("REMEDY_SELF_INJECT", "")
	t.Setenv("REMEDY_DESKTOP", "")
	t.Setenv("REMEDY_DESKTOP_SIDECAR", "")
	t.Chdir(t.TempDir())
	s, err := New(Config{
		HomeDir: home,
		Token:   statusRoutesToken,
		DBPath:  filepath.Join(home, "memory.db"),
		Version: "0.50.2-test",
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func doStatus(t *testing.T, s *Server, method, path, body string) (int, []byte, http.Header) {
	t.Helper()
	var r *http.Request
	if body != "" {
		r = httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
	} else {
		r = httptest.NewRequest(method, path, nil)
	}
	r.Header.Set("Authorization", "Bearer "+statusRoutesToken)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	return rr.Code, rr.Body.Bytes(), rr.Header()
}

func TestNotificationsListAndMarkRead(t *testing.T) {
	s := newStatusRoutesServer(t)
	now := float64(time.Now().Unix())
	items := []notification{
		{ID: "n1", Text: "Rent due tomorrow", CreatedTS: now, Source: "reminder", Importance: "normal", Channels: []string{}},
		{ID: "n2", Text: "Bins tonight", CreatedTS: now + 400, Source: "reminder", Importance: "normal", Channels: []string{}},
	}
	if err := writeNotifications(s.homeDir, items); err != nil {
		t.Fatal(err)
	}

	code, raw, _ := doStatus(t, s, http.MethodGet, "/api/notifications", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["count"] != float64(2) || body["unread"] != float64(2) {
		t.Fatalf("body=%v", body)
	}
	notes, _ := body["notifications"].([]any)
	if len(notes) != 2 {
		t.Fatalf("notifications=%v", notes)
	}

	code, raw, _ = doStatus(t, s, http.MethodPost, "/api/notifications/read", `{"ids":["n1"]}`)
	if code != http.StatusOK {
		t.Fatalf("mark status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["marked"] != float64(1) || body["unread"] != float64(1) {
		t.Fatalf("marked body=%v", body)
	}

	code, raw, _ = doStatus(t, s, http.MethodPost, "/api/notifications/read", `{"all":true}`)
	if code != http.StatusOK {
		t.Fatalf("mark all status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["unread"] != float64(0) {
		t.Fatalf("unread after all=%v", body)
	}

	code, raw, _ = doStatus(t, s, http.MethodGet, "/api/notifications?unread_only=true", "")
	if code != http.StatusOK {
		t.Fatalf("unread_only status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["count"] != float64(0) {
		t.Fatalf("unread_only count=%v", body)
	}
}

func TestMetricsJSONAndPrometheus(t *testing.T) {
	s := newStatusRoutesServer(t)
	DefaultMetrics.Counter("remedy_test_counter", nil).Inc(1)
	DefaultMetrics.Counter("remedy_tool_recovery_nudge_total", map[string]string{"kind": "tool_error"}).Inc(2)
	DefaultMetrics.Counter("remedy_tool_batch_errors_total", nil).Inc(1)
	DefaultMetrics.Counter("remedy_skill_run_total", map[string]string{"status": "ok"}).Inc(1)
	DefaultMetrics.Counter("remedy_skill_run_total", map[string]string{"status": "error"}).Inc(1)
	DefaultMetrics.Counter("remedy_prom_probe", nil).Inc(1)

	code, raw, hdr := doStatus(t, s, http.MethodGet, "/api/metrics", "")
	if code != http.StatusOK {
		t.Fatalf("metrics status=%d body=%s", code, raw)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["version"] != "0.50.2-test" {
		t.Fatalf("version=%v", body["version"])
	}
	if _, ok := body["metrics"]; !ok {
		t.Fatalf("missing metrics: %v", body)
	}
	if _, ok := body["health"]; !ok {
		t.Fatalf("missing health: %v", body)
	}
	agency, _ := body["agency"].(map[string]any)
	if agency == nil {
		t.Fatalf("agency=%v", body["agency"])
	}
	if agency["tool_recovery_nudges"].(float64) < 2 {
		t.Fatalf("agency=%v", agency)
	}
	if agency["tool_batch_errors"].(float64) < 1 {
		t.Fatalf("agency=%v", agency)
	}
	if agency["skill_run_ok"].(float64) < 1 || agency["skill_run_error"].(float64) < 1 {
		t.Fatalf("agency skill=%v", agency)
	}

	code, raw, hdr = doStatus(t, s, http.MethodGet, "/api/metrics?format=prometheus", "")
	if code != http.StatusOK {
		t.Fatalf("prom status=%d body=%s", code, raw)
	}
	ct := hdr.Get("Content-Type")
	if !strings.Contains(ct, "text/plain") {
		t.Fatalf("content-type=%q", ct)
	}
	if !strings.Contains(string(raw), "remedy_prom_probe") {
		t.Fatalf("prom body=%s", raw)
	}
}

func TestSelfImproveActivitySnapshot(t *testing.T) {
	s := newStatusRoutesServer(t)
	tick := map[string]any{"outcome": "noop", "ts": 1}
	if err := writeJSONAtomic(filepath.Join(ResolveHomeDir(s.homeDir), selfImproveLastName), tick); err != nil {
		t.Fatal(err)
	}

	code, raw, _ := doStatus(t, s, http.MethodGet, "/api/self-improve", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"enabled", "idle_ready", "idle_s", "idle_threshold_s", "last_tick", "update", "pending_ship"} {
		if _, ok := body[key]; !ok {
			t.Fatalf("missing %s in %v", key, body)
		}
	}
	last, _ := body["last_tick"].(map[string]any)
	if last["outcome"] != "noop" {
		t.Fatalf("last_tick=%v", last)
	}
	update, _ := body["update"].(map[string]any)
	if update["on_conflict"] != "origin_wins" {
		t.Fatalf("update=%v", update)
	}

	s.NoteUserActivity()
	code, raw, _ = doStatus(t, s, http.MethodGet, "/api/self-improve", "")
	if code != http.StatusOK {
		t.Fatalf("after note status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["last_user_activity"].(float64) <= 0 {
		t.Fatalf("last_user_activity=%v", body["last_user_activity"])
	}
}

func TestSelfImproveDisabledByEnv(t *testing.T) {
	s := newStatusRoutesServer(t)
	t.Setenv("REMEDY_SELF_INJECT", "0")
	code, raw, _ := doStatus(t, s, http.MethodGet, "/api/self-improve", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["enabled"] != false {
		t.Fatalf("enabled=%v", body["enabled"])
	}
	if body["idle_ready"] != false {
		t.Fatalf("idle_ready=%v", body["idle_ready"])
	}
}

func TestNotificationsRequireAuth(t *testing.T) {
	s := newStatusRoutesServer(t)
	r := httptest.NewRequest(http.MethodGet, "/api/notifications", nil)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	if rr.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d body=%s", rr.Code, rr.Body.String())
	}
}

func TestMetricLabelRedaction(t *testing.T) {
	reg := NewMetricsRegistry()
	secret := "sk-abcdefghijklmnopqrstuvwxyz0123"
	reg.Counter("remedy_leak_probe_total", map[string]string{"tool": secret}).Inc(1)
	text := reg.PrometheusText()
	if strings.Contains(text, secret) {
		t.Fatalf("secret leaked in prometheus text: %s", text)
	}
	if !strings.Contains(text, "[redacted]") {
		t.Fatalf("expected redaction: %s", text)
	}
	_ = os.Stderr
}
