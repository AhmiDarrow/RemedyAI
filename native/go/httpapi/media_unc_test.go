package httpapi

import (
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
)

// newMediaTestServer builds a server whose media roots are proj and a Remedy
// home under one temp dir.
func newMediaTestServer(t *testing.T) (s *Server, home, proj, remedyHome string) {
	t.Helper()
	home = t.TempDir()
	proj = filepath.Join(home, "proj")
	_ = os.MkdirAll(filepath.Join(proj, "assets"), 0o700)
	remedyHome = filepath.Join(home, ".remedy")
	_ = os.MkdirAll(filepath.Join(remedyHome, "auth"), 0o700)
	t.Setenv("REMEDY_HOME", remedyHome)
	t.Setenv("REMEDY_FILES_ROOT", proj)
	InvalidateConfigCache()
	_ = os.WriteFile(filepath.Join(remedyHome, "config.toml"), []byte(
		"project_path = "+strconvQuote(proj)+"\nhome_dir = "+strconvQuote(remedyHome)+"\n",
	), 0o600)
	srv, err := New(Config{HomeDir: remedyHome, Token: workspaceTestToken, DBPath: filepath.Join(remedyHome, "memory.db")})
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = srv.Close(); InvalidateConfigCache() })
	return srv, home, proj, remedyHome
}

// recordMediaStats swaps the media stat hook and records every path touched.
func recordMediaStats(t *testing.T) func() []string {
	t.Helper()
	var mu sync.Mutex
	var seen []string
	prev := mediaStat
	mediaStat = func(p string) (os.FileInfo, error) {
		mu.Lock()
		seen = append(seen, p)
		mu.Unlock()
		return prev(p)
	}
	t.Cleanup(func() { mediaStat = prev })
	return func() []string {
		mu.Lock()
		defer mu.Unlock()
		return append([]string(nil), seen...)
	}
}

// Regression (red-team): a chat image whose src is a UNC path
// (![x](\\attacker\share\x.png), e.g. planted by prompt injection in tool
// output) made /api/media os.Stat the share on Windows before the root
// check — an outbound SMB connection that hands the user's NTLM hash to the
// attacker. Network and device-namespace paths are refused up front and the
// filesystem is never touched for them.
func TestMediaRefusesNetworkAndDevicePaths(t *testing.T) {
	s, _, _, _ := newMediaTestServer(t)
	stats := recordMediaStats(t)
	for _, raw := range []string{
		`\\attacker.invalid\share\x.png`,
		`//attacker.invalid/share/x.png`,
		`\\?\UNC\attacker.invalid\share\x.png`,
		`\\.\pipe\x.png`,
		`//?/UNC/attacker.invalid/share/x.png`,
		`file:\\attacker.invalid\share\x.png`,
		`file:////attacker.invalid/share/x.png`,
	} {
		code, body := doWorkspaceReq(t, s, http.MethodGet, "/api/media?path="+url.QueryEscape(raw))
		if code != http.StatusBadRequest && code != http.StatusForbidden {
			t.Fatalf("%q: status=%d body=%s", raw, code, body)
		}
	}
	for _, p := range stats() {
		low := strings.ToLower(p)
		if strings.Contains(low, "attacker.invalid") || strings.Contains(low, `\\.\`) || strings.Contains(low, "pipe") {
			t.Fatalf("filesystem touched for a network path: %q", p)
		}
	}
}

// Out-of-root absolute paths are refused before any stat (the same
// ordering bug, local flavour).
func TestMediaChecksRootsBeforeStat(t *testing.T) {
	s, home, _, _ := newMediaTestServer(t)
	stats := recordMediaStats(t)
	outsideDir := filepath.Join(home, "elsewhere")
	_ = os.MkdirAll(outsideDir, 0o700)
	outside := filepath.Join(outsideDir, "x.png")
	_ = os.WriteFile(outside, []byte("\x89PNG\r\n\x1a\n"), 0o600)
	code, body := doWorkspaceReq(t, s, http.MethodGet, "/api/media?path="+url.QueryEscape(outside))
	if code != http.StatusForbidden {
		t.Fatalf("status=%d body=%s", code, body)
	}
	for _, p := range stats() {
		if strings.EqualFold(filepath.Clean(p), filepath.Clean(outside)) {
			t.Fatalf("stat'ed an out-of-root path: %q", p)
		}
	}
}

// Regression (red-team): an image-named symlink inside a media root that
// points at the auth store (or anywhere outside the roots) was served,
// because the jail was lexical.
func TestMediaRefusesSymlinkEscapes(t *testing.T) {
	if runtime.GOOS == "windows" {
		t.Skip("symlink creation needs developer mode on Windows")
	}
	s, home, proj, remedyHome := newMediaTestServer(t)
	secret := filepath.Join(remedyHome, "auth", "local_api_token")
	_ = os.WriteFile(secret, []byte("not-a-real-token"), 0o600)
	outside := filepath.Join(home, "private.txt")
	_ = os.WriteFile(outside, []byte("private"), 0o600)
	for name, target := range map[string]string{"leak.png": secret, "away.png": outside} {
		link := filepath.Join(proj, "assets", name)
		if err := os.Symlink(target, link); err != nil {
			t.Fatal(err)
		}
		code, body := doWorkspaceReq(t, s, http.MethodGet, "/api/media?path="+url.QueryEscape(link))
		if code != http.StatusForbidden {
			t.Fatalf("%s -> %s: status=%d body=%s", name, target, code, body)
		}
		if strings.Contains(string(body), "not-a-real-token") || strings.Contains(string(body), "private") && code == http.StatusOK {
			t.Fatalf("%s leaked target bytes", name)
		}
	}
	// An in-root symlink to an in-root image still works.
	real := filepath.Join(proj, "assets", "real.png")
	_ = os.WriteFile(real, []byte("\x89PNG\r\n\x1a\n"), 0o600)
	alias := filepath.Join(proj, "assets", "alias.png")
	_ = os.Symlink(real, alias)
	if code, body := doWorkspaceReq(t, s, http.MethodGet, "/api/media?path="+url.QueryEscape(alias)); code != http.StatusOK {
		t.Fatalf("in-root alias: status=%d body=%s", code, body)
	}
}
