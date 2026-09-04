package httpapi

import (
	"archive/zip"
	"bytes"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestSkillPacksGetAndPut(t *testing.T) {
	s, _ := newSkillsTestServer(t)

	code, raw := doSkillsReq(t, s, http.MethodGet, "/api/skills/packs", "")
	if code != http.StatusOK {
		t.Fatalf("get packs status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if _, ok := out["packs"]; !ok {
		t.Fatalf("missing packs: %v", out)
	}
	if _, ok := out["active_budget"]; !ok {
		t.Fatalf("missing budget: %v", out)
	}

	code, raw = doSkillsReq(t, s, http.MethodPut, "/api/skills/packs",
		`{"packs":{"core":{"skills":["demo-skill"]}},"enabled":["core"]}`)
	if code != http.StatusOK {
		t.Fatalf("put packs status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["status"] != "ok" {
		t.Fatalf("put out=%v", out)
	}
	code, raw = doSkillsReq(t, s, http.MethodGet, "/api/skills/packs", "")
	if code != http.StatusOK {
		t.Fatalf("reload status=%d", code)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	enabled, _ := out["enabled"].([]any)
	if len(enabled) != 1 || enabled[0] != "core" {
		t.Fatalf("enabled=%v", out["enabled"])
	}
}

func TestSkillStatusQuarantineBody(t *testing.T) {
	s, home := newSkillsTestServer(t)

	code, raw := doSkillsReq(t, s, http.MethodPost, "/api/skills/demo-skill/status",
		`{"status":"disabled"}`)
	if code != http.StatusOK {
		t.Fatalf("status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["status"] != "disabled" {
		t.Fatalf("out=%v", out)
	}

	code, raw = doSkillsReq(t, s, http.MethodPost, "/api/skills/demo-skill/quarantine",
		`{"quarantine":true}`)
	if code != http.StatusOK {
		t.Fatalf("quarantine status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["quarantine"] != true {
		t.Fatalf("out=%v", out)
	}

	code, raw = doSkillsReq(t, s, http.MethodPut, "/api/skills/demo-skill/body",
		`{"body":"# Updated\n\nNew instructions.\n"}`)
	if code != http.StatusOK {
		t.Fatalf("body status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["status"] != "saved" {
		t.Fatalf("out=%v", out)
	}
	md, err := os.ReadFile(filepath.Join(home, "skills", "demo-skill", "SKILL.md"))
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(string(md), "New instructions") {
		t.Fatalf("SKILL.md=%s", md)
	}
}

func TestSkillFeedbackLearningMetrics(t *testing.T) {
	s, home := newSkillsTestServer(t)

	// Mark demo as auto-generated for learning summary.
	mdPath := filepath.Join(home, "skills", "demo-skill", "SKILL.md")
	rawMD, _ := os.ReadFile(mdPath)
	updated := strings.Replace(string(rawMD), "---\n", "---\nauto_generated: true\n", 1)
	_ = os.WriteFile(mdPath, []byte(updated), 0o600)

	code, raw := doSkillsReq(t, s, http.MethodPost, "/api/skills/demo-skill/feedback",
		`{"success":true}`)
	if code != http.StatusOK {
		t.Fatalf("feedback status=%d body=%s", code, raw)
	}

	code, raw = doSkillsReq(t, s, http.MethodGet, "/api/skills/learning/summary", "")
	if code != http.StatusOK {
		t.Fatalf("learning status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if _, ok := out["recent"]; !ok {
		t.Fatalf("missing recent: %v", out)
	}

	code, raw = doSkillsReq(t, s, http.MethodGet, "/api/skills/metrics/reuse", "")
	if code != http.StatusOK {
		t.Fatalf("metrics status=%d body=%s", code, raw)
	}
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if _, ok := out["skills_tracked"]; !ok {
		t.Fatalf("metrics=%v", out)
	}
}

func TestSkillArchiveUnusedDryRun(t *testing.T) {
	s, home := newSkillsTestServer(t)
	mdPath := filepath.Join(home, "skills", "demo-skill", "SKILL.md")
	rawMD, _ := os.ReadFile(mdPath)
	updated := strings.Replace(string(rawMD), "---\n", "---\nauto_generated: true\nstatus: discovered\n", 1)
	_ = os.WriteFile(mdPath, []byte(updated), 0o600)

	code, raw := doSkillsReq(t, s, http.MethodPost, "/api/skills/archive-unused",
		`{"days":90,"dry_run":true}`)
	if code != http.StatusOK {
		t.Fatalf("archive status=%d body=%s", code, raw)
	}
	var out map[string]any
	if err := json.Unmarshal(raw, &out); err != nil {
		t.Fatal(err)
	}
	if out["dry_run"] != true {
		t.Fatalf("out=%v", out)
	}
	cands, _ := out["candidates"].([]any)
	if len(cands) < 1 {
		t.Fatalf("expected candidates for never-used learned skill: %v", out)
	}
}

func TestSkillsExportImportRoundTrip(t *testing.T) {
	s, home := newSkillsTestServer(t)

	r := httptest.NewRequest(http.MethodPost, "/api/skills/export",
		strings.NewReader(`{"names":["demo-skill"]}`))
	r.Header.Set("Content-Type", "application/json")
	r.Header.Set("Authorization", "Bearer "+skillsTestToken)
	r.Host = "127.0.0.1:7400"
	r.RemoteAddr = "127.0.0.1:12345"
	rr := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr, r)
	if rr.Code != http.StatusOK {
		t.Fatalf("export status=%d body=%s", rr.Code, rr.Body.String())
	}
	ct := rr.Header().Get("Content-Type")
	if !strings.Contains(ct, "zip") {
		t.Fatalf("content-type=%q", ct)
	}
	zipBytes := rr.Body.Bytes()
	if len(zipBytes) < 10 {
		t.Fatalf("zip too small")
	}
	zr, err := zip.NewReader(bytes.NewReader(zipBytes), int64(len(zipBytes)))
	if err != nil {
		t.Fatal(err)
	}
	if len(zr.File) < 1 {
		t.Fatal("empty zip")
	}

	// Import into a fresh home as a new name via rewriting zip is heavy —
	// instead import the same pack after removing the skill dir.
	_ = os.RemoveAll(filepath.Join(home, "skills", "demo-skill"))

	var buf bytes.Buffer
	w := multipartNew(t, &buf, "file", "pack.zip", zipBytes)
	req := httptest.NewRequest(http.MethodPost, "/api/skills/import", &buf)
	req.Header.Set("Content-Type", w)
	req.Header.Set("Authorization", "Bearer "+skillsTestToken)
	req.Host = "127.0.0.1:7400"
	req.RemoteAddr = "127.0.0.1:12345"
	rr2 := httptest.NewRecorder()
	s.Handler().ServeHTTP(rr2, req)
	if rr2.Code != http.StatusOK {
		t.Fatalf("import status=%d body=%s", rr2.Code, rr2.Body.String())
	}
	var out map[string]any
	if err := json.Unmarshal(rr2.Body.Bytes(), &out); err != nil {
		t.Fatal(err)
	}
	if out["quarantine"] != true {
		t.Fatalf("import out=%v", out)
	}
	imported, _ := out["imported"].(float64)
	if imported < 1 {
		t.Fatalf("imported=%v", out)
	}
}

func multipartNew(t *testing.T, buf *bytes.Buffer, field, filename string, data []byte) string {
	t.Helper()
	boundary := "----remedytestboundary"
	buf.WriteString("--" + boundary + "\r\n")
	buf.WriteString(`Content-Disposition: form-data; name="` + field + `"; filename="` + filename + `"` + "\r\n")
	buf.WriteString("Content-Type: application/zip\r\n\r\n")
	buf.Write(data)
	buf.WriteString("\r\n--" + boundary + "--\r\n")
	return "multipart/form-data; boundary=" + boundary
}
