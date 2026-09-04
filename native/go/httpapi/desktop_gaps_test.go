package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"net/url"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const desktopGapsToken = "tok-desktop-gaps-test"

func newDesktopGapsServer(t *testing.T) *Server {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_DEV_ROOT", "")
	t.Chdir(t.TempDir())
	s, err := New(Config{
		HomeDir: home,
		Token:   desktopGapsToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func doGaps(t *testing.T, s *Server, method, path, body string) (int, []byte) {
	t.Helper()
	var r *http.Request
	if body != "" {
		r = httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
	} else {
		r = httptest.NewRequest(method, path, nil)
	}
	r.Header.Set("Authorization", "Bearer "+desktopGapsToken)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	return rr.Code, rr.Body.Bytes()
}

func TestListAgentsAndCommands(t *testing.T) {
	s := newDesktopGapsServer(t)
	code, raw := doGaps(t, s, http.MethodGet, "/api/agents", "")
	if code != http.StatusOK {
		t.Fatalf("agents status=%d body=%s", code, raw)
	}
	var agents map[string]any
	if err := json.Unmarshal(raw, &agents); err != nil {
		t.Fatal(err)
	}
	list, _ := agents["agents"].([]any)
	if len(list) < 4 {
		t.Fatalf("agents=%v", agents)
	}

	code, raw = doGaps(t, s, http.MethodGet, "/api/commands", "")
	if code != http.StatusOK {
		t.Fatalf("commands status=%d body=%s", code, raw)
	}
	var cmds map[string]any
	if err := json.Unmarshal(raw, &cmds); err != nil {
		t.Fatal(err)
	}
	clist, _ := cmds["commands"].([]any)
	if len(clist) < 10 {
		t.Fatalf("commands=%v", cmds)
	}
}

func TestAppCommandTakeEmpty(t *testing.T) {
	s := newDesktopGapsServer(t)
	code, raw := doGaps(t, s, http.MethodGet, "/api/app/command?take=1", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["command"] != nil {
		t.Fatalf("expected null command, got %v", out["command"])
	}
	_ = s.appCmd.Enqueue("focus_composer", nil)
	code, raw = doGaps(t, s, http.MethodGet, "/api/app/command?take=1", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	cmd, _ := out["command"].(map[string]any)
	if cmd == nil || cmd["action"] != "focus_composer" {
		t.Fatalf("command=%v", out["command"])
	}
}

func TestScratchRoundTrip(t *testing.T) {
	s := newDesktopGapsServer(t)
	code, raw := doGaps(t, s, http.MethodPut, "/api/scratch", `{"session_id":"s1","text":"hello pad"}`)
	if code != http.StatusOK {
		t.Fatalf("put status=%d body=%s", code, raw)
	}
	code, raw = doGaps(t, s, http.MethodGet, "/api/scratch?session_id=s1", "")
	if code != http.StatusOK {
		t.Fatalf("get status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["text"] != "hello pad" {
		t.Fatalf("text=%v", out["text"])
	}
}

func TestEditFromMessage(t *testing.T) {
	s := newDesktopGapsServer(t)
	code, raw := doGaps(t, s, http.MethodPost, "/api/sessions", `{"title":"Edit"}`)
	if code != http.StatusOK {
		t.Fatalf("create=%d %s", code, raw)
	}
	var sess ChatSession
	if err := json.Unmarshal(raw, &sess); err != nil {
		t.Fatal(err)
	}
	u, err := s.sessions.AddMessage(sess.ID, "user", "first prompt", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.sessions.AddMessage(sess.ID, "assistant", "reply", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	code, raw = doGaps(t, s, http.MethodPost, "/api/sessions/"+sess.ID+"/messages/"+u.ID+"/edit", "")
	if code != http.StatusOK {
		t.Fatalf("edit status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["status"] != "ready_to_edit" || out["content"] != "first prompt" {
		t.Fatalf("out=%v", out)
	}
	if out["reverted_count"].(float64) < 1 {
		t.Fatalf("reverted=%v", out["reverted_count"])
	}
}

func TestBulkProjectAndImport(t *testing.T) {
	s := newDesktopGapsServer(t)
	proj := filepath.Join(t.TempDir(), "proj")
	if err := os.MkdirAll(proj, 0o700); err != nil {
		t.Fatal(err)
	}
	code, raw := doGaps(t, s, http.MethodPost, "/api/sessions", `{"title":"A"}`)
	if code != http.StatusOK {
		t.Fatalf("create=%d %s", code, raw)
	}
	var a ChatSession
	_ = json.Unmarshal(raw, &a)
	code, raw = doGaps(t, s, http.MethodPost, "/api/sessions", `{"title":"B"}`)
	if code != http.StatusOK {
		t.Fatalf("create=%d %s", code, raw)
	}
	var b ChatSession
	_ = json.Unmarshal(raw, &b)

	body := `{"session_ids":["` + a.ID + `","` + b.ID + `","missing"],"project_path":` + gapsJSON(proj) + `}`
	code, raw = doGaps(t, s, http.MethodPost, "/api/sessions/bulk-project", body)
	if code != http.StatusOK {
		t.Fatalf("bulk status=%d body=%s", code, raw)
	}
	var bulk map[string]any
	if err := json.Unmarshal(raw, &bulk); err != nil {
		t.Fatal(err)
	}
	updated, _ := bulk["updated"].([]any)
	missing, _ := bulk["missing"].([]any)
	if len(updated) != 2 || len(missing) != 1 {
		t.Fatalf("bulk=%v", bulk)
	}

	export := "# Remedy Session\nTitle: Imported\n\n===== USER =====\nhello import\n\n===== ASSISTANT =====\nhi\n"
	code, raw = doGaps(t, s, http.MethodPost, "/api/sessions/import", gapsJSON(map[string]any{"text": export}))
	if code != http.StatusOK {
		t.Fatalf("import status=%d body=%s", code, raw)
	}
	var imp map[string]any
	if err := json.Unmarshal(raw, &imp); err != nil {
		t.Fatal(err)
	}
	if imp["imported_messages"].(float64) != 2 {
		t.Fatalf("import=%v", imp)
	}
}

func TestProjectsScan(t *testing.T) {
	s := newDesktopGapsServer(t)
	root := t.TempDir()
	_ = os.WriteFile(filepath.Join(root, "main.py"), []byte("print(1)\n"), 0o600)
	_ = os.WriteFile(filepath.Join(root, "app.ts"), []byte(" const x=1\n"), 0o600)
	code, raw := doGaps(t, s, http.MethodPost, "/api/projects/scan?path="+mustQuery(root), "")
	if code != http.StatusOK {
		t.Fatalf("scan status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	counts, _ := out["file_counts"].(map[string]any)
	if counts["python"].(float64) < 1 || counts["typescript"].(float64) < 1 {
		t.Fatalf("counts=%v", counts)
	}
}

func TestSelfInjectRoundsEmpty(t *testing.T) {
	s := newDesktopGapsServer(t)
	code, raw := doGaps(t, s, http.MethodGet, "/api/self-inject/rounds?limit=5", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	rounds, _ := out["rounds"].([]any)
	if rounds == nil {
		t.Fatalf("rounds missing: %v", out)
	}
	if out["serve_started_utc"] == nil || out["serve_started_utc"] == "" {
		t.Fatalf("serve_started_utc missing")
	}
}

func TestContinuityNanoswarmPresence(t *testing.T) {
	s := newDesktopGapsServer(t)
	for _, path := range []string{
		"/api/continuity/dashboard",
		"/api/nanoswarm/status",
		"/api/nanoswarm/token/status",
		"/api/coordination/presence",
	} {
		code, raw := doGaps(t, s, http.MethodGet, path, "")
		if code != http.StatusOK {
			t.Fatalf("%s status=%d body=%s", path, code, raw)
		}
	}
}

func TestDiagnosticsSnapshot(t *testing.T) {
	s := newDesktopGapsServer(t)
	code, raw := doGaps(t, s, http.MethodGet, "/api/diagnostics", "")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	for _, key := range []string{"ok", "overall", "issues", "remedy", "rmb", "vision", "hardware", "providers", "computer"} {
		if _, ok := out[key]; !ok {
			t.Fatalf("missing %s in %v", key, out)
		}
	}
}

func gapsJSON(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		panic(err)
	}
	return string(b)
}

func mustQuery(p string) string {
	return url.QueryEscape(p)
}
