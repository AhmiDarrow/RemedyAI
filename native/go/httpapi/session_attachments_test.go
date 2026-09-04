package httpapi

import (
	"bytes"
	"encoding/base64"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

const sessionAttTestToken = "tok-session-att-test-not-a-secret"

func newSessionAttTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	InvalidateConfigCache()
	s, err := New(Config{
		HomeDir: home,
		Token:   sessionAttTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doSessionAttJSON(t *testing.T, s *Server, method, path string, body any) (int, map[string]any, string, []byte) {
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
	req.Header.Set("Authorization", "Bearer "+sessionAttTestToken)
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	raw := rr.Body.Bytes()
	text := string(raw)
	var payload map[string]any
	_ = json.Unmarshal(raw, &payload)
	return rr.Code, payload, text, raw
}

func createAttTestSession(t *testing.T, s *Server) string {
	t.Helper()
	code, body, text, _ := doSessionAttJSON(t, s, http.MethodPost, "/api/sessions", map[string]any{
		"title": "Attachments",
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

func TestUploadAttachmentHappyPath(t *testing.T) {
	s, home := newSessionAttTestServer(t)
	sid := createAttTestSession(t, s)

	payload := []byte("hello composer")
	code, body, text, _ := doSessionAttJSON(t, s, http.MethodPost, "/api/sessions/"+sid+"/attachments", map[string]any{
		"filename":     "note.txt",
		"content_type": "text/plain",
		"data_base64":  base64.StdEncoding.EncodeToString(payload),
	})
	if code != http.StatusOK {
		t.Fatalf("upload status=%d body=%s", code, text)
	}
	if body["name"] != "note.txt" {
		t.Fatalf("name=%v body=%s", body["name"], text)
	}
	if body["mime"] != "text/plain" {
		t.Fatalf("mime=%v", body["mime"])
	}
	if body["size"] != float64(len(payload)) {
		t.Fatalf("size=%v", body["size"])
	}
	if body["is_text"] != true {
		t.Fatalf("is_text=%v", body["is_text"])
	}
	if body["is_image"] != false {
		t.Fatalf("is_image=%v", body["is_image"])
	}
	id, _ := body["id"].(string)
	if id == "" {
		t.Fatalf("missing id: %s", text)
	}
	path, _ := body["path"].(string)
	if path == "" {
		t.Fatalf("missing path: %s", text)
	}
	wantDir := filepath.Join(home, "attachments", safeSessionID(sid))
	if !strings.HasPrefix(filepath.Clean(path), filepath.Clean(wantDir)) {
		t.Fatalf("path %q not under %q", path, wantDir)
	}
	got, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("disk bytes=%q", got)
	}
}

func TestUploadAttachmentMissingSession(t *testing.T) {
	s, _ := newSessionAttTestServer(t)
	code, body, text, _ := doSessionAttJSON(t, s, http.MethodPost, "/api/sessions/no-such-session/attachments", map[string]any{
		"filename":     "note.txt",
		"content_type": "text/plain",
		"data_base64":  base64.StdEncoding.EncodeToString([]byte("x")),
	})
	if code != http.StatusNotFound {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["detail"] != "Session not found" {
		t.Fatalf("detail=%v body=%s", body["detail"], text)
	}
}

func TestGetAttachmentHappyPath(t *testing.T) {
	s, _ := newSessionAttTestServer(t)
	sid := createAttTestSession(t, s)

	payload := []byte("hello there")
	code, meta, text, _ := doSessionAttJSON(t, s, http.MethodPost, "/api/sessions/"+sid+"/attachments", map[string]any{
		"filename":     "note.txt",
		"content_type": "text/plain",
		"data_base64":  base64.StdEncoding.EncodeToString(payload),
	})
	if code != http.StatusOK {
		t.Fatalf("upload status=%d body=%s", code, text)
	}
	name, _ := meta["name"].(string)
	if name == "" {
		name = "note.txt"
	}

	req := httptest.NewRequest(http.MethodGet, "/api/sessions/"+sid+"/attachments/"+name, nil)
	req.Header.Set("Authorization", "Bearer "+sessionAttTestToken)
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("get status=%d body=%s", rr.Code, rr.Body.String())
	}
	got, err := io.ReadAll(rr.Body)
	if err != nil {
		t.Fatal(err)
	}
	if !bytes.Equal(got, payload) {
		t.Fatalf("get body=%q", got)
	}
}

func TestGetAttachmentNotFound(t *testing.T) {
	s, _ := newSessionAttTestServer(t)
	sid := createAttTestSession(t, s)
	code, body, text, _ := doSessionAttJSON(t, s, http.MethodGet, "/api/sessions/"+sid+"/attachments/nope.txt", nil)
	if code != http.StatusNotFound {
		t.Fatalf("status=%d body=%s", code, text)
	}
	if body["detail"] != "Attachment not found" {
		t.Fatalf("detail=%v body=%s", body["detail"], text)
	}
}

func TestGetAttachmentPathTraversal(t *testing.T) {
	s, home := newSessionAttTestServer(t)
	sid := createAttTestSession(t, s)
	secret := filepath.Join(home, "config.toml")
	if err := os.WriteFile(secret, []byte("llm_api_key = 'secret'\n"), 0o644); err != nil {
		t.Fatal(err)
	}

	attempts := []string{
		"..",
		".",
		"../config.toml",
		"..\\config.toml",
	}
	for _, attempt := range attempts {
		req := httptest.NewRequest(http.MethodGet, "/api/sessions/"+sid+"/attachments/"+attempt, nil)
		req.Header.Set("Authorization", "Bearer "+sessionAttTestToken)
		req.Host = "127.0.0.1:7400"
		req.RemoteAddr = "127.0.0.1:12345"
		rr := httptest.NewRecorder()
		s.Handler().ServeHTTP(rr, req)
		if rr.Code == http.StatusOK {
			t.Fatalf("attempt %q returned 200", attempt)
		}
		if strings.Contains(rr.Body.String(), "secret") {
			t.Fatalf("attempt %q leaked secret: %s", attempt, rr.Body.String())
		}
	}
}

func TestUploadAttachmentRequiresAuth(t *testing.T) {
	s, _ := newSessionAttTestServer(t)
	sid := createAttTestSession(t, s)
	raw, _ := json.Marshal(map[string]any{
		"filename":    "note.txt",
		"data_base64": base64.StdEncoding.EncodeToString([]byte("x")),
	})
	req := httptest.NewRequest(http.MethodPost, "/api/sessions/"+sid+"/attachments", bytes.NewReader(raw))
	req.Header.Set("Content-Type", "application/json")
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d body=%s", rr.Code, rr.Body.String())
	}
}

func TestUploadAttachmentEmptyAndBadBase64(t *testing.T) {
	s, _ := newSessionAttTestServer(t)
	sid := createAttTestSession(t, s)

	code, _, text, _ := doSessionAttJSON(t, s, http.MethodPost, "/api/sessions/"+sid+"/attachments", map[string]any{
		"filename":    "note.txt",
		"data_base64": "",
	})
	if code != http.StatusBadRequest {
		t.Fatalf("empty status=%d body=%s", code, text)
	}

	code, _, text, _ = doSessionAttJSON(t, s, http.MethodPost, "/api/sessions/"+sid+"/attachments", map[string]any{
		"filename":    "note.txt",
		"data_base64": "!!!!",
	})
	if code != http.StatusBadRequest {
		t.Fatalf("bad b64 status=%d body=%s", code, text)
	}
}

func TestUploadAttachmentSiblingSessionIsolated(t *testing.T) {
	s, _ := newSessionAttTestServer(t)
	sid1 := createAttTestSession(t, s)
	sid2 := createAttTestSession(t, s)

	code, _, text, _ := doSessionAttJSON(t, s, http.MethodPost, "/api/sessions/"+sid1+"/attachments", map[string]any{
		"filename":    "private.txt",
		"data_base64": base64.StdEncoding.EncodeToString([]byte("secret")),
	})
	if code != http.StatusOK {
		t.Fatalf("upload status=%d body=%s", code, text)
	}

	code, body, text, _ := doSessionAttJSON(t, s, http.MethodGet, "/api/sessions/"+sid2+"/attachments/private.txt", nil)
	if code != http.StatusNotFound {
		t.Fatalf("cross-session get status=%d body=%s", code, text)
	}
	if body["detail"] != "Attachment not found" {
		t.Fatalf("detail=%v", body["detail"])
	}
}
