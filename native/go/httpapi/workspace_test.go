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

const workspaceTestToken = "tok-workspace-test-not-a-secret"

func newWorkspaceTestServer(t *testing.T, project string) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_FILES_ROOT", project)
	t.Setenv("REMEDY_PROJECT_PATH", project)
	cfgPath := filepath.Join(home, "config.toml")
	cfg := "project_path = " + strconvQuote(project) + "\naccess_scope = \"project\"\n"
	if err := os.WriteFile(cfgPath, []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	InvalidateConfigCache()

	s, err := New(Config{
		HomeDir: home,
		Token:   workspaceTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func strconvQuote(s string) string {
	b, _ := json.Marshal(s)
	return string(b)
}

func doWorkspaceReq(t *testing.T, s *Server, method, path string) (int, []byte) {
	t.Helper()
	r := httptest.NewRequest(method, path, nil)
	r.Header.Set("Authorization", "Bearer "+workspaceTestToken)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	return rr.Code, rr.Body.Bytes()
}

func TestFilesListAndJail(t *testing.T) {
	proj := t.TempDir()
	if err := os.WriteFile(filepath.Join(proj, "ok.txt"), []byte("hi"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Mkdir(filepath.Join(proj, "src"), 0o700); err != nil {
		t.Fatal(err)
	}
	s, _ := newWorkspaceTestServer(t, proj)

	code, raw := doWorkspaceReq(t, s, http.MethodGet, "/api/files?path=.")
	if code != http.StatusOK {
		t.Fatalf("list status=%d body=%s", code, raw)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["error"] != nil && body["error"] != "" {
		t.Fatalf("unexpected error: %#v", body["error"])
	}
	files, _ := body["files"].([]any)
	names := map[string]bool{}
	for _, f := range files {
		m, _ := f.(map[string]any)
		names[m["name"].(string)] = true
	}
	if !names["ok.txt"] || !names["src"] {
		t.Fatalf("names=%v body=%s", names, raw)
	}

	code, raw = doWorkspaceReq(t, s, http.MethodGet, "/api/files?path=..")
	if code != http.StatusOK {
		t.Fatalf("escape status=%d", code)
	}
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	if body["error"] == nil || body["error"] == "" {
		t.Fatalf("expected jail error, got %s", raw)
	}
	files, _ = body["files"].([]any)
	if len(files) != 0 {
		t.Fatalf("escape leaked files: %s", raw)
	}
}

func TestFilesRejectsSystemPaths(t *testing.T) {
	proj := t.TempDir()
	s, _ := newWorkspaceTestServer(t, proj)

	paths := []string{
		`C:\Windows\System32\config\SAM`,
		`C:\Users\Administrator\Desktop\..\..\Windows\win.ini`,
		`../../../Windows/System32/drivers/etc/hosts`,
	}
	for _, p := range paths {
		u := "/api/files?path=" + url.QueryEscape(p)
		code, raw := doWorkspaceReq(t, s, http.MethodGet, u)
		if code != http.StatusOK {
			t.Fatalf("%s status=%d", p, code)
		}
		var body map[string]any
		if err := json.Unmarshal(raw, &body); err != nil {
			t.Fatal(err)
		}
		if body["error"] == nil || body["error"] == "" {
			t.Fatalf("expected error for %s: %s", p, raw)
		}
		files, _ := body["files"].([]any)
		if len(files) != 0 {
			t.Fatalf("leaked listing for %s: %s", p, raw)
		}
	}
}

func TestFilesSearchJails(t *testing.T) {
	root := t.TempDir()
	proj := filepath.Join(root, "proj")
	outside := filepath.Join(root, "secret")
	if err := os.MkdirAll(filepath.Join(proj, "src"), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(proj, "src", "ok.py"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(outside, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(outside, "leak.txt"), []byte("nope"), 0o600); err != nil {
		t.Fatal(err)
	}
	s, _ := newWorkspaceTestServer(t, proj)

	code, raw := doWorkspaceReq(t, s, http.MethodGet,
		"/api/files/search?query=leak&path="+url.QueryEscape(outside))
	if code != http.StatusBadRequest {
		t.Fatalf("escape search status=%d body=%s", code, raw)
	}

	missing := filepath.Join(root, "would_create")
	code, raw = doWorkspaceReq(t, s, http.MethodGet,
		"/api/files/search?query=x&path="+url.QueryEscape(missing))
	if code != http.StatusBadRequest {
		t.Fatalf("missing path status=%d body=%s", code, raw)
	}
	if _, err := os.Stat(missing); !os.IsNotExist(err) {
		t.Fatalf("search must not mkdir %s", missing)
	}

	code, raw = doWorkspaceReq(t, s, http.MethodGet, "/api/files/search?query=ok&path=src")
	if code != http.StatusOK {
		t.Fatalf("in-project search status=%d body=%s", code, raw)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	results, _ := body["results"].([]any)
	found := false
	for _, r := range results {
		m, _ := r.(map[string]any)
		if m["name"] == "ok.py" {
			found = true
		}
	}
	if !found {
		t.Fatalf("ok.py missing: %s", raw)
	}
}

func TestWorkspaceBasics(t *testing.T) {
	proj := t.TempDir()
	if err := os.WriteFile(filepath.Join(proj, "readme.md"), []byte("hi"), 0o600); err != nil {
		t.Fatal(err)
	}
	s, _ := newWorkspaceTestServer(t, proj)

	code, raw := doWorkspaceReq(t, s, http.MethodGet, "/api/workspace")
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	pp, _ := body["project_path"].(string)
	if !strings.EqualFold(filepath.Clean(pp), filepath.Clean(proj)) {
		t.Fatalf("project_path=%q want %q", pp, proj)
	}
	entries, _ := body["entries"].([]any)
	if len(entries) == 0 {
		t.Fatalf("expected entries: %s", raw)
	}
}

func TestFilesSessionProjectOverride(t *testing.T) {
	home := t.TempDir()
	projA := filepath.Join(home, "a")
	projB := filepath.Join(home, "b")
	_ = os.MkdirAll(projA, 0o700)
	_ = os.MkdirAll(projB, 0o700)
	_ = os.WriteFile(filepath.Join(projA, "a-only.txt"), []byte("a"), 0o600)
	_ = os.WriteFile(filepath.Join(projB, "b-only.txt"), []byte("b"), 0o600)

	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_FILES_ROOT", projA)
	InvalidateConfigCache()
	_ = os.WriteFile(filepath.Join(home, "config.toml"),
		[]byte("project_path = "+strconvQuote(projA)+"\n"), 0o600)

	s, err := New(Config{
		HomeDir: home,
		Token:   workspaceTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })

	pp := projB
	sess, err := s.sessions.Create(createSessionRequest{Title: "B", ProjectPath: &pp})
	if err != nil {
		t.Fatal(err)
	}

	code, raw := doWorkspaceReq(t, s, http.MethodGet,
		"/api/files?path=.&session_id="+url.QueryEscape(sess.ID))
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var body map[string]any
	if err := json.Unmarshal(raw, &body); err != nil {
		t.Fatal(err)
	}
	names := map[string]bool{}
	for _, f := range body["files"].([]any) {
		m := f.(map[string]any)
		names[m["name"].(string)] = true
	}
	if !names["b-only.txt"] {
		t.Fatalf("session project not used: %s", raw)
	}
	if names["a-only.txt"] {
		t.Fatalf("listed wrong project: %s", raw)
	}
}

func TestMediaServesAndJails(t *testing.T) {
	home := t.TempDir()
	proj := filepath.Join(home, "proj")
	assets := filepath.Join(proj, "assets")
	_ = os.MkdirAll(assets, 0o700)
	png := filepath.Join(assets, "hero.png")
	pngBytes := []byte{
		0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a, 0x00, 0x00, 0x00, 0x0d,
		0x49, 0x48, 0x44, 0x52, 0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,
		0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53, 0xde, 0x00, 0x00, 0x00,
		0x0c, 0x49, 0x44, 0x41, 0x54, 0x78, 0x9c, 0x63, 0xf8, 0x0f, 0x00, 0x00,
		0x01, 0x01, 0x00, 0x05, 0x18, 0xd8, 0x4e, 0x00, 0x00, 0x00, 0x00, 0x49,
		0x45, 0x4e, 0x44, 0xae, 0x42, 0x60, 0x82,
	}
	if err := os.WriteFile(png, pngBytes, 0o600); err != nil {
		t.Fatal(err)
	}
	outside := filepath.Join(home, "secret.png")
	_ = os.WriteFile(outside, pngBytes, 0o600)

	remedyHome := filepath.Join(home, ".remedy")
	_ = os.MkdirAll(filepath.Join(remedyHome, "attachments", "sess"), 0o700)
	att := filepath.Join(remedyHome, "attachments", "sess", "shot.png")
	_ = os.WriteFile(att, pngBytes, 0o600)
	_ = os.MkdirAll(filepath.Join(remedyHome, "auth"), 0o700)
	secretImg := filepath.Join(remedyHome, "auth", "leak.png")
	_ = os.WriteFile(secretImg, pngBytes, 0o600)

	t.Setenv("REMEDY_HOME", remedyHome)
	t.Setenv("REMEDY_FILES_ROOT", proj)
	InvalidateConfigCache()
	_ = os.WriteFile(filepath.Join(remedyHome, "config.toml"), []byte(
		"project_path = "+strconvQuote(proj)+"\nhome_dir = "+strconvQuote(remedyHome)+"\n",
	), 0o600)

	s, err := New(Config{
		HomeDir: remedyHome,
		Token:   workspaceTestToken,
		DBPath:  filepath.Join(remedyHome, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })

	// Relative project path
	code, raw := doWorkspaceReq(t, s, http.MethodGet,
		"/api/media?path="+url.QueryEscape("assets/hero.png"))
	if code != http.StatusOK {
		t.Fatalf("rel media status=%d body=%s", code, raw)
	}
	if len(raw) < 8 || string(raw[:8]) != "\x89PNG\r\n\x1a\n" {
		t.Fatalf("not png: %x", raw[:min(8, len(raw))])
	}

	// Attachment under remedy home
	code, raw = doWorkspaceReq(t, s, http.MethodGet,
		"/api/media?path="+url.QueryEscape(att))
	if code != http.StatusOK {
		t.Fatalf("att media status=%d body=%s", code, raw)
	}

	// Outside project → 403
	code, raw = doWorkspaceReq(t, s, http.MethodGet,
		"/api/media?path="+url.QueryEscape(outside))
	if code != http.StatusForbidden && code != http.StatusNotFound {
		t.Fatalf("outside status=%d body=%s", code, raw)
	}

	// Auth secrets → 403
	code, raw = doWorkspaceReq(t, s, http.MethodGet,
		"/api/media?path="+url.QueryEscape(secretImg))
	if code != http.StatusForbidden {
		t.Fatalf("auth media status=%d body=%s", code, raw)
	}
	if !strings.Contains(strings.ToLower(string(raw)), "protected") &&
		!strings.Contains(strings.ToLower(string(raw)), "secret") {
		t.Fatalf("auth deny body=%s", raw)
	}

	// SVG refused
	svg := filepath.Join(proj, "x.svg")
	_ = os.WriteFile(svg, []byte(`<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>`), 0o600)
	code, raw = doWorkspaceReq(t, s, http.MethodGet,
		"/api/media?path="+url.QueryEscape(svg))
	if code != http.StatusUnsupportedMediaType && code != http.StatusForbidden {
		t.Fatalf("svg status=%d body=%s", code, raw)
	}
}

func TestFilesRequiresAuth(t *testing.T) {
	proj := t.TempDir()
	s, _ := newWorkspaceTestServer(t, proj)
	r := httptest.NewRequest(http.MethodGet, "/api/files?path=.", nil)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	if rr.Code != http.StatusUnauthorized {
		t.Fatalf("status=%d want 401", rr.Code)
	}
}
