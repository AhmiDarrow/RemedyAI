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

const computerTestToken = "tok-computer-test-not-a-secret"

func newComputerTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   computerTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doComputerJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string) {
	t.Helper()
	var rdr *bytes.Reader
	if body != nil {
		raw, _ := json.Marshal(body)
		rdr = bytes.NewReader(raw)
	} else {
		rdr = bytes.NewReader(nil)
	}
	req := httptest.NewRequest(method, path, rdr)
	req.Header.Set("Authorization", "Bearer "+computerTestToken)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	raw := rr.Body.Bytes()
	var payload map[string]any
	_ = json.Unmarshal(raw, &payload)
	return rr.Code, payload, string(raw)
}

func TestComputerHostHelloAndStatus(t *testing.T) {
	s, home := newComputerTestServer(t)
	code, body, text := doComputerJSON(t, s, http.MethodPost, "/api/computer/host/hello", map[string]any{
		"client": "desktop",
		"bounds": map[string]any{"x": 1, "y": 2, "width": 100, "height": 50},
		"scale":  1.5,
		"session_id": "sess-1",
	})
	if code != http.StatusOK {
		t.Fatalf("hello status=%d body=%s", code, text)
	}
	if body["ok"] != true || body["poller_required"] != true {
		t.Fatalf("hello body=%s", text)
	}
	if body["host_connected"] != false {
		t.Fatalf("hello alone must not mark poller connected: %s", text)
	}

	code, body, text = doComputerJSON(t, s, http.MethodGet, "/api/computer/host/status", nil)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["focused_session_id"] != "sess-1" {
		t.Fatalf("focused=%v", body["focused_session_id"])
	}
	bounds, _ := body["browser_bounds"].(map[string]any)
	if bounds["width"] != float64(100) || bounds["scale"] != 1.5 {
		t.Fatalf("bounds=%v", bounds)
	}
	if !strings.Contains(strOr(body["jobs_root"], ""), filepath.Join(home, "computer", "jobs")) &&
		!strings.Contains(strOr(body["jobs_root"], ""), `computer`+string(os.PathSeparator)+`jobs`) {
		t.Fatalf("jobs_root=%v home=%s", body["jobs_root"], home)
	}
}

func TestComputerJobsClaimCompleteCancel(t *testing.T) {
	s, _ := newComputerTestServer(t)
	b := s.bridge()
	job := &ComputerJob{
		ID: "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa", Action: "navigate",
		Payload: map[string]any{"url": "https://example.com"},
		Status: "pending", SessionID: "s1",
		CreatedAt: "2026-01-01T00:00:00Z", UpdatedAt: "2026-01-01T00:00:00Z",
	}
	b.mu.Lock()
	b.pollIdleEmpty = false
	if err := b.writeJob(job); err != nil {
		b.mu.Unlock()
		t.Fatal(err)
	}
	b.mu.Unlock()

	code, body, text := doComputerJSON(t, s, http.MethodGet, "/api/computer/jobs/next?driver=rust", nil)
	if code != http.StatusOK {
		t.Fatalf("next status=%d body=%s", code, text)
	}
	got, _ := body["job"].(map[string]any)
	if got == nil || got["id"] != job.ID || got["status"] != "running" {
		t.Fatalf("claimed job=%s", text)
	}

	code, body, text = doComputerJSON(t, s, http.MethodPost, "/api/computer/jobs/"+job.ID+"/complete", map[string]any{
		"ok": true, "result": map[string]any{"ok": true, "url": "https://example.com"},
	})
	if code != http.StatusOK {
		t.Fatalf("complete status=%d body=%s", code, text)
	}
	got, _ = body["job"].(map[string]any)
	if got["status"] != "done" {
		t.Fatalf("complete job=%s", text)
	}

	job2 := &ComputerJob{
		ID: "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", Action: "click",
		Payload: map[string]any{"ref": "e1"}, Status: "pending",
		CreatedAt: "2026-01-01T00:00:00Z", UpdatedAt: "2026-01-01T00:00:00Z",
	}
	b.mu.Lock()
	b.pollIdleEmpty = false
	_ = b.writeJob(job2)
	b.mu.Unlock()
	code, body, text = doComputerJSON(t, s, http.MethodPost, "/api/computer/jobs/"+job2.ID+"/cancel", nil)
	if code != http.StatusOK {
		t.Fatalf("cancel status=%d body=%s", code, text)
	}
	got, _ = body["job"].(map[string]any)
	if got["status"] != "cancelled" {
		t.Fatalf("cancel job=%s", text)
	}
}

func TestComputerCaptureBrowserishRequiresBounds(t *testing.T) {
	s, _ := newComputerTestServer(t)
	code, body, text := doComputerJSON(t, s, http.MethodPost, "/api/computer/capture", map[string]any{
		"label": "browser-rail",
	})
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["ok"] != false {
		t.Fatalf("expected ok=false: %s", text)
	}
}

func TestComputerA11yPushRejectsShortJobID(t *testing.T) {
	s, _ := newComputerTestServer(t)
	code, _, text := doComputerJSON(t, s, http.MethodPost, "/api/computer/a11y/push", map[string]any{
		"job_id": "short", "elements": []any{},
	})
	if code != http.StatusBadRequest {
		t.Fatalf("status=%d body=%s", code, text)
	}
}

func TestComputerUICommandPeekAndAck(t *testing.T) {
	s, _ := newComputerTestServer(t)
	b := s.bridge()
	b.setUICommand(map[string]any{"action": "open_browser", "url": "https://x.test", "job_id": "j1"})
	code, body, text := doComputerJSON(t, s, http.MethodGet, "/api/computer/ui/command", nil)
	if code != http.StatusOK {
		t.Fatalf("peek status=%d body=%s", code, text)
	}
	cmd, _ := body["command"].(map[string]any)
	if cmd["action"] != "open_browser" {
		t.Fatalf("cmd=%s", text)
	}
	code, _, text = doComputerJSON(t, s, http.MethodPost, "/api/computer/ui/command/ack", map[string]any{"job_id": "j1"})
	if code != http.StatusOK {
		t.Fatalf("ack status=%d body=%s", code, text)
	}
	code, body, text = doComputerJSON(t, s, http.MethodGet, "/api/computer/ui/command", nil)
	if code != http.StatusOK {
		t.Fatalf("peek2 status=%d body=%s", code, text)
	}
	if body["command"] != nil {
		t.Fatalf("expected cleared command: %s", text)
	}
}

func (b *HostBridge) setUICommand(command map[string]any) {
	b.mu.Lock()
	defer b.mu.Unlock()
	cmd := cloneMap(command)
	b.uiCommand = cmd
	raw, _ := json.Marshal(cmd)
	_ = os.WriteFile(b.uiPath, raw, 0o600)
}
