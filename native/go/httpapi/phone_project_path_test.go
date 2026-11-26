package httpapi

import (
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"path/filepath"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

func doPhoneGaps(t *testing.T, s *Server, method, path, body string) (int, []byte) {
	t.Helper()
	r := httptest.NewRequest(method, path, strings.NewReader(body))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer "+desktopGapsToken)
	r.Header.Set(connect.ConnectHopHeader, "1")
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	return rr.Code, rr.Body.Bytes()
}

// A phone can't point a session at a folder the PC never chose: a new project
// folder is picked at the PC, like the locked project_path setting.
func TestPhoneCannotBindSessionToNewFolder(t *testing.T) {
	s := newDesktopGapsServer(t)
	known := filepath.Join(t.TempDir(), "known")
	other := filepath.Join(t.TempDir(), "elsewhere")

	// The PC binds a session to a folder.
	code, raw := doGaps(t, s, http.MethodPost, "/api/sessions", gapsJSON(map[string]any{"title": "pc", "project_path": known}))
	if code != http.StatusOK {
		t.Fatalf("pc create status=%d body=%s", code, raw)
	}
	// The phone makes a plain session.
	code, raw = doPhoneGaps(t, s, http.MethodPost, "/api/sessions", `{"title":"phone"}`)
	if code != http.StatusOK {
		t.Fatalf("phone create status=%d body=%s", code, raw)
	}
	var sess ChatSession
	_ = json.Unmarshal(raw, &sess)

	for _, tc := range []struct{ method, path, body string }{
		{http.MethodPatch, "/api/sessions/" + sess.ID, gapsJSON(map[string]any{"project_path": other})},
		{http.MethodPost, "/api/sessions", gapsJSON(map[string]any{"title": "x", "project_path": other})},
		{http.MethodPost, "/api/sessions/bulk-project", gapsJSON(map[string]any{"session_ids": []string{sess.ID}, "project_path": other})},
	} {
		code, raw = doPhoneGaps(t, s, tc.method, tc.path, tc.body)
		if code != http.StatusForbidden {
			t.Fatalf("%s %s from phone: status=%d body=%s, want 403", tc.method, tc.path, code, raw)
		}
	}
	got, _, _ := s.sessions.Get(sess.ID)
	if got.ProjectPath != nil {
		t.Fatalf("phone moved the session to %q", *got.ProjectPath)
	}

	// Moving among folders the PC already chose, and clearing, still work.
	code, raw = doPhoneGaps(t, s, http.MethodPatch, "/api/sessions/"+sess.ID, gapsJSON(map[string]any{"project_path": known}))
	if code != http.StatusOK {
		t.Fatalf("phone move to known folder status=%d body=%s", code, raw)
	}
	code, raw = doPhoneGaps(t, s, http.MethodPatch, "/api/sessions/"+sess.ID, `{"project_path":null}`)
	if code != http.StatusOK {
		t.Fatalf("phone clear status=%d body=%s", code, raw)
	}
	// The PC itself still picks any folder.
	code, raw = doGaps(t, s, http.MethodPatch, "/api/sessions/"+sess.ID, gapsJSON(map[string]any{"project_path": other}))
	if code != http.StatusOK {
		t.Fatalf("pc patch status=%d body=%s", code, raw)
	}
}
