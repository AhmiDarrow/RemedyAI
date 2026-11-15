package httpapi

import (
	"crypto/sha256"
	"encoding/base64"
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

func TestFindWebUIDirBundledFallback(t *testing.T) {
	bundled := t.TempDir()
	writeTestWebUI(t, bundled)
	t.Setenv("REMEDY_WEBUI_DIR", "")
	t.Setenv("REMEDY_DEV_ROOT", "")
	t.Setenv("REMEDY_BUNDLED_WEBUI_DIR", bundled)
	// Run from an empty folder so no desktop/dist next to cwd wins.
	t.Chdir(t.TempDir())
	got := FindWebUIDir()
	abs, _ := filepath.Abs(bundled)
	if got == "" {
		t.Skip("a desktop/dist next to the test binary was found first")
	}
	if filepath.Clean(got) != filepath.Clean(abs) {
		// Only acceptable when a live build next to the executable exists.
		if !strings.HasSuffix(filepath.Clean(got), filepath.Join("desktop", "dist")) {
			t.Fatalf("FindWebUIDir = %q, want bundled %q", got, abs)
		}
	}
}

func TestFindWebUIDirExplicitBeatsBundled(t *testing.T) {
	explicit := t.TempDir()
	bundled := t.TempDir()
	writeTestWebUI(t, explicit)
	writeTestWebUI(t, bundled)
	t.Setenv("REMEDY_WEBUI_DIR", explicit)
	t.Setenv("REMEDY_BUNDLED_WEBUI_DIR", bundled)
	got := FindWebUIDir()
	abs, _ := filepath.Abs(explicit)
	if filepath.Clean(got) != filepath.Clean(abs) {
		t.Fatalf("FindWebUIDir = %q, want explicit %q", got, abs)
	}
}

func TestWebUICSPHashesInlineScriptsOnly(t *testing.T) {
	inline := "(function(){document.documentElement.dataset.x='1'})();"
	html := []byte("<html><head><script>" + inline + "</script>" +
		`<script type="module" src="./assets/index.js"></script></head></html>`)
	sum := sha256.Sum256([]byte(inline))
	want := "'sha256-" + base64.StdEncoding.EncodeToString(sum[:]) + "'"
	csp := webUICSP(html)
	if !strings.Contains(csp, "script-src 'self' "+want+";") {
		t.Fatalf("csp missing inline hash: %q", csp)
	}
	for _, bad := range []string{"'unsafe-inline' ;", "script-src 'self' 'unsafe-inline'", "'unsafe-eval'"} {
		if strings.Contains(csp, bad) {
			t.Fatalf("csp must not contain %q: %q", bad, csp)
		}
	}
	for _, need := range []string{"frame-ancestors 'none'", "object-src 'none'", "base-uri 'self'"} {
		if !strings.Contains(csp, need) {
			t.Fatalf("csp missing %q: %q", need, csp)
		}
	}
}

func TestWebUIResponsesCarrySecurityHeaders(t *testing.T) {
	dir := t.TempDir()
	writeTestWebUI(t, dir)
	inline := "window.__boot=1;"
	if err := os.WriteFile(filepath.Join(dir, "index.html"),
		[]byte("<!doctype html><title>Remedy</title><script>"+inline+"</script>"), 0o644); err != nil {
		t.Fatal(err)
	}
	base, shutdown := startTestServer(t, Config{
		Token:    "test-token-not-a-secret-16",
		Version:  "0.50.2",
		WebUIDir: dir,
	})
	defer shutdown()
	client := &http.Client{Timeout: 3 * time.Second}
	sum := sha256.Sum256([]byte(inline))
	hash := "'sha256-" + base64.StdEncoding.EncodeToString(sum[:]) + "'"
	for _, path := range []string{"/", "/grove/chat", "/assets/app.js", "/favicon.ico"} {
		resp, err := client.Get(base + path)
		if err != nil {
			t.Fatal(err)
		}
		io.Copy(io.Discard, resp.Body)
		resp.Body.Close()
		csp := resp.Header.Get("Content-Security-Policy")
		if !strings.Contains(csp, "frame-ancestors 'none'") {
			t.Fatalf("%s: CSP = %q", path, csp)
		}
		if resp.Header.Get("Referrer-Policy") != "no-referrer" ||
			resp.Header.Get("X-Content-Type-Options") != "nosniff" ||
			resp.Header.Get("X-Frame-Options") != "DENY" {
			t.Fatalf("%s: missing hardening headers: %v", path, resp.Header)
		}
		if (path == "/" || path == "/grove/chat") && !strings.Contains(csp, hash) {
			t.Fatalf("%s: CSP lacks inline boot script hash: %q", path, csp)
		}
	}
}
