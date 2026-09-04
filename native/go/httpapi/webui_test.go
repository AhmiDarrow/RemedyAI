package httpapi

import (
	"io"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func writeTestWebUI(t *testing.T, root string) {
	t.Helper()
	if err := os.MkdirAll(filepath.Join(root, "assets"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "index.html"), []byte("<!doctype html><title>Remedy</title>"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "assets", "app.js"), []byte("console.log('ok')"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(root, "favicon.ico"), []byte("ico"), 0o644); err != nil {
		t.Fatal(err)
	}
}

func TestFindWebUIDirEnv(t *testing.T) {
	dir := t.TempDir()
	writeTestWebUI(t, dir)
	t.Setenv("REMEDY_WEBUI_DIR", dir)
	t.Setenv("REMEDY_DEV_ROOT", "")
	got := FindWebUIDir()
	if got == "" {
		t.Fatal("expected webui dir")
	}
	abs, _ := filepath.Abs(dir)
	if filepath.Clean(got) != filepath.Clean(abs) {
		t.Fatalf("FindWebUIDir = %q, want %q", got, abs)
	}
}

func TestWebUIServesSPA(t *testing.T) {
	dir := t.TempDir()
	writeTestWebUI(t, dir)
	base, shutdown := startTestServer(t, Config{
		Token:    "test-token-not-a-secret-16",
		Version:  "0.50.2",
		WebUIDir: dir,
	})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}

	resp, err := client.Get(base + "/")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET / status = %d", resp.StatusCode)
	}
	if !strings.Contains(string(body), "Remedy") {
		t.Fatalf("index body = %q", body)
	}
	if cc := resp.Header.Get("Cache-Control"); !strings.Contains(cc, "no-store") {
		t.Fatalf("Cache-Control = %q", cc)
	}

	resp, err = client.Get(base + "/assets/app.js")
	if err != nil {
		t.Fatal(err)
	}
	body, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(body), "console.log") {
		t.Fatalf("assets status=%d body=%q", resp.StatusCode, body)
	}

	resp, err = client.Get(base + "/favicon.ico")
	if err != nil {
		t.Fatal(err)
	}
	body, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || string(body) != "ico" {
		t.Fatalf("favicon status=%d body=%q", resp.StatusCode, body)
	}

	// Deep link falls back to index.html
	resp, err = client.Get(base + "/grove/chat")
	if err != nil {
		t.Fatal(err)
	}
	body, _ = io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK || !strings.Contains(string(body), "Remedy") {
		t.Fatalf("spa fallback status=%d body=%q", resp.StatusCode, body)
	}

	// Reserved API prefix via SPA catch-all must 404 (specific /api routes still win).
	resp, err = client.Get(base + "/api/does-not-exist-route")
	if err != nil {
		t.Fatal(err)
	}
	io.Copy(io.Discard, resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusNotFound && resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("missing api route status = %d", resp.StatusCode)
	}
}

func TestWebUIMissingHelperPage(t *testing.T) {
	base, shutdown := startTestServer(t, Config{
		Token:    "test-token-not-a-secret-16",
		Version:  "0.50.2",
		WebUIDir: filepath.Join(t.TempDir(), "missing-webui"),
	})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	resp, err := client.Get(base + "/")
	if err != nil {
		t.Fatal(err)
	}
	body, _ := io.ReadAll(resp.Body)
	resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	if !strings.Contains(string(body), "WebUI assets not bundled") {
		t.Fatalf("body = %q", body)
	}
}
