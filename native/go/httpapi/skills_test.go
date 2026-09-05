package httpapi

import (
	"crypto/ed25519"
	"crypto/rand"
	"encoding/base64"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func generateTestCatalogKey(t *testing.T) (pubB64 string, priv ed25519.PrivateKey, err error) {
	t.Helper()
	pub, priv, err := ed25519.GenerateKey(rand.Reader)
	if err != nil {
		return "", nil, err
	}
	return base64.StdEncoding.EncodeToString(pub), priv, nil
}

func signTestCatalog(t *testing.T, priv ed25519.PrivateKey, data []byte) string {
	t.Helper()
	sig := ed25519.Sign(priv, data)
	return base64.StdEncoding.EncodeToString(sig)
}

const skillsTestToken = "tok-skills-test-not-a-secret"

func newSkillsTestServer(t *testing.T) (*Server, string) {
	t.Helper()
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_DEV_ROOT", "")
	// Avoid scanning the monorepo cwd for seed skills in unit tests.
	cwd := t.TempDir()
	t.Chdir(cwd)

	skillsDir := filepath.Join(home, "skills", "demo-skill")
	if err := os.MkdirAll(skillsDir, 0o700); err != nil {
		t.Fatal(err)
	}
	md := "---\n" +
		"name: demo-skill\n" +
		"description: >\n" +
		"  Demo skill for listing and detail routes.\n" +
		"version: 1.2.0\n" +
		"author: Test\n" +
		"tags: [demo, test]\n" +
		"---\n\n" +
		"# Demo\n\nDo the demo thing.\n"
	if err := os.WriteFile(filepath.Join(skillsDir, "SKILL.md"), []byte(md), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(skillsDir, "scripts"), 0o700); err != nil {
		t.Fatal(err)
	}
	_ = os.WriteFile(filepath.Join(skillsDir, "scripts", "run.py"), []byte("print(1)\n"), 0o600)

	s, err := New(Config{
		HomeDir: home,
		Token:   skillsTestToken,
		DBPath:  filepath.Join(home, "memory.db"),
	})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s, home
}

func doSkillsReq(t *testing.T, s *Server, method, path string, body string) (int, []byte) {
	t.Helper()
	var r *http.Request
	if body != "" {
		r = httptest.NewRequest(method, path, strings.NewReader(body))
		r.Header.Set("Content-Type", "application/json")
	} else {
		r = httptest.NewRequest(method, path, nil)
	}
	r.Header.Set("Authorization", "Bearer "+skillsTestToken)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	return rr.Code, rr.Body.Bytes()
}

func TestListAndGetSkills(t *testing.T) {
	s, _ := newSkillsTestServer(t)

	code, raw := doSkillsReq(t, s, http.MethodGet, "/api/skills", "")
	if code != http.StatusOK {
		t.Fatalf("list status=%d body=%s", code, raw)
	}
	var list []map[string]any
	if err := json.Unmarshal(raw, &list); err != nil {
		t.Fatalf("list json: %v (%s)", err, raw)
	}
	if len(list) != 1 {
		t.Fatalf("len=%d want 1; body=%s", len(list), raw)
	}
	if list[0]["name"] != "demo-skill" {
		t.Fatalf("name=%v", list[0]["name"])
	}
	if list[0]["status"] != "active" {
		t.Fatalf("status=%v want active", list[0]["status"])
	}
	tags, _ := list[0]["tags"].([]any)
	if len(tags) != 2 {
		t.Fatalf("tags=%v", list[0]["tags"])
	}

	code, raw = doSkillsReq(t, s, http.MethodGet, "/api/skills?q=demo", "")
	if code != http.StatusOK {
		t.Fatalf("search status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &list); err != nil || len(list) != 1 {
		t.Fatalf("search list=%v err body=%s", list, raw)
	}

	code, raw = doSkillsReq(t, s, http.MethodGet, "/api/skills/demo-skill", "")
	if code != http.StatusOK {
		t.Fatalf("get status=%d body=%s", code, raw)
	}
	var detail map[string]any
	if err := json.Unmarshal(raw, &detail); err != nil {
		t.Fatal(err)
	}
	if detail["name"] != "demo-skill" || detail["version"] != "1.2.0" {
		t.Fatalf("detail=%v", detail)
	}
	body, _ := detail["body"].(string)
	if !strings.Contains(body, "Do the demo thing") {
		t.Fatalf("body=%q", body)
	}
	scripts, _ := detail["scripts"].([]any)
	if len(scripts) != 1 || scripts[0] != "scripts/run.py" {
		t.Fatalf("scripts=%v", detail["scripts"])
	}

	code, _ = doSkillsReq(t, s, http.MethodGet, "/api/skills/missing-skill", "")
	if code != http.StatusNotFound {
		t.Fatalf("missing status=%d", code)
	}
}

func TestStatusSkillsCount(t *testing.T) {
	s, _ := newSkillsTestServer(t)
	req := httptest.NewRequest(http.MethodGet, "/api/status", nil)
	req.Header.Set("Authorization", "Bearer "+skillsTestToken)
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, req)
	if rr.Code != http.StatusOK {
		t.Fatalf("status=%d", rr.Code)
	}
	var body map[string]any
	if err := json.Unmarshal(rr.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	n, _ := body["skills_count"].(float64)
	if int(n) != 1 {
		t.Fatalf("skills_count=%v want 1; body=%v", body["skills_count"], body)
	}
}

func TestDeleteUserSkill(t *testing.T) {
	s, home := newSkillsTestServer(t)
	skillDir := filepath.Join(home, "skills", "demo-skill")
	if !dirExists(skillDir) {
		t.Fatalf("missing fixture skill dir %s", skillDir)
	}

	code, raw := doSkillsReq(t, s, http.MethodDelete, "/api/skills/demo-skill", "")
	if code != http.StatusOK {
		t.Fatalf("delete status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["status"] != "deleted" || out["removed_files"] != true {
		t.Fatalf("out=%v", out)
	}
	if dirExists(skillDir) {
		t.Fatal("skill dir still present after delete")
	}

	code, _ = doSkillsReq(t, s, http.MethodDelete, "/api/skills/does-not-exist", "")
	if code != http.StatusNotFound {
		t.Fatalf("missing delete status=%d", code)
	}
}

func TestParseSkillFrontmatterFolded(t *testing.T) {
	raw := "---\nname: x\ndescription: >\n  hello\n  world\nversion: 2.0.0\ntags: [a, b]\n---\n\nBody here\n"
	fm, body, ok := parseSkillFrontmatter(raw)
	if !ok {
		t.Fatal("parse failed")
	}
	if fm.Name != "x" || fm.Version != "2.0.0" {
		t.Fatalf("fm=%+v", fm)
	}
	if fm.Description != "hello world" {
		t.Fatalf("desc=%q", fm.Description)
	}
	if len(fm.Tags) != 2 {
		t.Fatalf("tags=%v", fm.Tags)
	}
	if !strings.Contains(body, "Body here") {
		t.Fatalf("body=%q", body)
	}
}

func TestLibrarySuggestDismissAndEmpty(t *testing.T) {
	s, home := newSkillsTestServer(t)
	// Empty cache → suggest returns null without 502.
	code, raw := doSkillsReq(t, s, http.MethodGet, "/api/skills/library/suggest?q=implement+accessibility+review+pipeline", "")
	if code != http.StatusOK {
		t.Fatalf("suggest status=%d body=%s", code, raw)
	}
	var payload map[string]any
	if err := json.Unmarshal(raw, &payload); err != nil {
		t.Fatal(err)
	}
	if payload["suggestion"] != nil {
		t.Fatalf("expected nil suggestion with empty catalog, got %#v", payload["suggestion"])
	}
	if payload["needs_refresh"] != true {
		t.Fatalf("needs_refresh=%v", payload["needs_refresh"])
	}

	code, raw = doSkillsReq(t, s, http.MethodPost, "/api/skills/library/suggest/dismiss",
		`{"skill_id":"a11y-design-review","session_id":"s1"}`)
	if code != http.StatusOK {
		t.Fatalf("dismiss status=%d body=%s", code, raw)
	}
	_ = home
}

func TestLibraryCatalogFromCache(t *testing.T) {
	s, home := newSkillsTestServer(t)
	cacheDir := filepath.Join(home, "cache", "skills")
	if err := os.MkdirAll(cacheDir, 0o700); err != nil {
		t.Fatal(err)
	}

	// Build a tiny unsigned-invalid cache first — catalog endpoint should 502
	// without a valid signature when remote is unreachable / disallowed.
	cat := skillsCatalog{
		Version:     "1",
		GeneratedAt: "2026-01-01T00:00:00Z",
		Repository:  libraryRepo,
		Skills: []librarySkillEntry{{
			ID:          "a11y-design-review",
			Name:        "a11y-design-review",
			Description: "Design-side accessibility review",
			Version:     "1.0.0",
			Author:      "Remedy Official",
			Tags:        []string{"design", "a11y"},
			DownloadURL: defaultCatalogURL(),
			Checksum:    "sha256:" + strings.Repeat("ab", 32),
			Status:      "published",
		}},
	}
	data, _ := json.Marshal(cat)

	// Sign with a throwaway key won't match production pubkey. Instead, skip
	// remote by using REMEDY_SKILLS_DEV + local verify key via injecting a
	// correctly signed cache using crypto/ed25519 in-test.
	pub, priv, err := generateTestCatalogKey(t)
	if err != nil {
		t.Fatal(err)
	}
	sig := signTestCatalog(t, priv, data)
	if err := os.WriteFile(filepath.Join(cacheDir, "catalog.json"), data, 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(cacheDir, "catalog.json.sig"), []byte(sig+"\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv("REMEDY_SKILLS_DEV", "1")
	t.Setenv("REMEDY_SKILLS_CATALOG_PUBKEY", pub)

	code, raw := doSkillsReq(t, s, http.MethodGet, "/api/skills/library/catalog", "")
	if code != http.StatusOK {
		t.Fatalf("catalog status=%d body=%s", code, raw)
	}
	var got skillsCatalog
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got.Source != "cache" || len(got.Skills) != 1 || got.Skills[0].ID != "a11y-design-review" {
		t.Fatalf("catalog=%+v", got)
	}

	code, raw = doSkillsReq(t, s, http.MethodGet, "/api/skills/library/search?q=a11y", "")
	if code != http.StatusOK {
		t.Fatalf("search status=%d body=%s", code, raw)
	}
	var search map[string]any
	if err := json.Unmarshal(raw, &search); err != nil {
		t.Fatal(err)
	}
	if int(search["total"].(float64)) != 1 {
		t.Fatalf("search=%v", search)
	}

	code, raw = doSkillsReq(t, s, http.MethodGet,
		"/api/skills/library/suggest?q=implement+accessibility+design+review+pipeline", "")
	if code != http.StatusOK {
		t.Fatalf("suggest status=%d body=%s", code, raw)
	}
	var sug map[string]any
	if err := json.Unmarshal(raw, &sug); err != nil {
		t.Fatal(err)
	}
	if sug["suggestion"] == nil {
		t.Fatalf("expected suggestion, got %s", raw)
	}

	code, raw = doSkillsReq(t, s, http.MethodGet, "/api/skills/library/updates", "")
	if code != http.StatusOK {
		t.Fatalf("updates status=%d body=%s", code, raw)
	}
	var upd map[string]any
	if err := json.Unmarshal(raw, &upd); err != nil {
		t.Fatal(err)
	}
	updates, _ := upd["updates"].([]any)
	if updates == nil {
		t.Fatalf("updates missing: %s", raw)
	}
}
