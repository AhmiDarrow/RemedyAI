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

const sessionExtrasToken = "tok-session-extras-test"

func newSessionExtrasServer(t *testing.T) *Server {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_DEV_ROOT", "")
	t.Chdir(t.TempDir())
	s, err := New(Config{
		HomeDir: home,
		Token:   sessionExtrasToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func doSessionExtras(t *testing.T, s *Server, method, path, body string) (int, []byte) {
	t.Helper()
	var r *http.Request
	if body != "" {
		r = httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
	} else {
		r = httptest.NewRequest(method, path, nil)
	}
	r.Header.Set("Authorization", "Bearer "+sessionExtrasToken)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	return rr.Code, rr.Body.Bytes()
}

func createExtrasSession(t *testing.T, s *Server) string {
	t.Helper()
	code, raw := doSessionExtras(t, s, http.MethodPost, "/api/sessions", `{"title":"Extras"}`)
	if code != http.StatusOK {
		t.Fatalf("create status=%d body=%s", code, raw)
	}
	var sess ChatSession
	if err := json.Unmarshal(raw, &sess); err != nil {
		t.Fatal(err)
	}
	return sess.ID
}

func TestExportSessionTxtAndMd(t *testing.T) {
	s := newSessionExtrasServer(t)
	sid := createExtrasSession(t, s)
	_, err := s.sessions.AddMessage(sid, "user", "hello export", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.sessions.AddMessage(sid, "assistant", "hi back", nil, nil)
	if err != nil {
		t.Fatal(err)
	}

	code, raw := doSessionExtras(t, s, http.MethodGet, "/api/sessions/"+sid+"/export?format=txt", "")
	if code != http.StatusOK {
		t.Fatalf("export txt status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	text, _ := out["text"].(string)
	if !strings.Contains(text, "# Remedy Session") || !strings.Contains(text, "===== USER =====") {
		t.Fatalf("txt body=%q", text)
	}
	if out["format"] != "txt" {
		t.Fatalf("format=%v", out["format"])
	}
	fn, _ := out["filename"].(string)
	if !strings.HasSuffix(fn, ".txt") {
		t.Fatalf("filename=%q", fn)
	}

	code, raw = doSessionExtras(t, s, http.MethodGet, "/api/sessions/"+sid+"/export?format=md", "")
	if code != http.StatusOK {
		t.Fatalf("export md status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	text, _ = out["text"].(string)
	if !strings.Contains(text, "**User**") || out["format"] != "md" {
		t.Fatalf("md out=%v", out)
	}
}

func TestSteerSessionNoTurnAndClaimed(t *testing.T) {
	s := newSessionExtrasServer(t)
	sid := createExtrasSession(t, s)

	code, raw := doSessionExtras(t, s, http.MethodPost, "/api/sessions/"+sid+"/steer",
		`{"message":"nudge me"}`)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["steered"] != false || out["reason"] != "no_turn" {
		t.Fatalf("want no_turn, got %v", out)
	}

	epoch, _, ok := s.claims.TryClaim(sid)
	if !ok {
		t.Fatal("claim failed")
	}
	defer s.claims.Release(sid, &epoch)

	code, raw = doSessionExtras(t, s, http.MethodPost, "/api/sessions/"+sid+"/steer",
		`{"message":"course correct"}`)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["steered"] != true || out["reason"] != "ok" {
		t.Fatalf("want steered ok, got %v", out)
	}
	nudges := s.claims.DrainNudges(sid)
	if len(nudges) != 1 || nudges[0] != "course correct" {
		t.Fatalf("nudges=%v", nudges)
	}
	msgs, err := s.sessions.ListMessages(sid, 50, 0)
	if err != nil {
		t.Fatal(err)
	}
	found := false
	for _, m := range msgs {
		if m.Role == "user" && contentAsString(m.Content) == "course correct" {
			found = true
		}
	}
	if !found {
		t.Fatalf("steered message not persisted; msgs=%v", msgs)
	}
}

func TestSessionTodosFromProject(t *testing.T) {
	s := newSessionExtrasServer(t)
	proj := t.TempDir()
	buildDir := filepath.Join(proj, ".remedy-build")
	if err := os.MkdirAll(buildDir, 0o700); err != nil {
		t.Fatal(err)
	}
	todos := `[{"id":"a1","content":"Ship extras","status":"pending"}]`
	if err := os.WriteFile(filepath.Join(buildDir, "todos.json"), []byte(todos), 0o600); err != nil {
		t.Fatal(err)
	}
	code, raw := doSessionExtras(t, s, http.MethodPost, "/api/sessions",
		`{"title":"Todos","project_path":`+mustJSON(proj)+`}`)
	if code != http.StatusOK {
		t.Fatalf("create status=%d body=%s", code, raw)
	}
	var sess ChatSession
	if err := json.Unmarshal(raw, &sess); err != nil {
		t.Fatal(err)
	}
	code, raw = doSessionExtras(t, s, http.MethodGet, "/api/sessions/"+sess.ID+"/todos", "")
	if code != http.StatusOK {
		t.Fatalf("todos status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	list, _ := out["todos"].([]any)
	if len(list) != 1 {
		t.Fatalf("todos=%v", out)
	}
}

func TestSessionTimelineAndTimeTravel(t *testing.T) {
	s := newSessionExtrasServer(t)
	sid := createExtrasSession(t, s)
	u, err := s.sessions.AddMessage(sid, "user", "step one", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.sessions.AddMessage(sid, "assistant", "reply one", nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	_, err = s.sessions.AddMessage(sid, "user", "step two", nil, nil)
	if err != nil {
		t.Fatal(err)
	}

	code, raw := doSessionExtras(t, s, http.MethodGet, "/api/sessions/"+sid+"/timeline", "")
	if code != http.StatusOK {
		t.Fatalf("timeline status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	steps, _ := out["steps"].([]any)
	if len(steps) < 2 {
		t.Fatalf("steps=%v", out)
	}

	code, raw = doSessionExtras(t, s, http.MethodPost, "/api/sessions/"+sid+"/time-travel",
		`{"message_id":"`+u.ID+`"}`)
	if code != http.StatusOK {
		t.Fatalf("time-travel status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["status"] != "restored" {
		t.Fatalf("out=%v", out)
	}
	msgs, err := s.sessions.ListMessages(sid, 50, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(msgs) != 0 {
		t.Fatalf("expected all reverted, got %d", len(msgs))
	}
}

func TestSessionCommandHelpAndReset(t *testing.T) {
	s := newSessionExtrasServer(t)
	sid := createExtrasSession(t, s)
	_, _ = s.sessions.AddMessage(sid, "user", "bye", nil, nil)

	code, raw := doSessionExtras(t, s, http.MethodPost, "/api/sessions/"+sid+"/command",
		`{"command":"/help"}`)
	if code != http.StatusOK {
		t.Fatalf("help status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	text, _ := out["text"].(string)
	if !strings.Contains(text, "/help") || out["session_id"] != sid {
		t.Fatalf("help out=%v", out)
	}

	code, raw = doSessionExtras(t, s, http.MethodPost, "/api/sessions/"+sid+"/command",
		`{"command":"/reset"}`)
	if code != http.StatusOK {
		t.Fatalf("reset status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["action"] != "reset_session" {
		t.Fatalf("reset out=%v", out)
	}
	msgs, err := s.sessions.ListMessages(sid, 50, 0)
	if err != nil {
		t.Fatal(err)
	}
	if len(msgs) != 0 {
		t.Fatalf("messages after reset=%d", len(msgs))
	}
}

func mustJSON(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}
