//go:build windows

package httpapi

import (
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"testing"
)

// A junction needs no privilege on Windows and filepath.EvalSymlinks does not
// follow it, so the real-path check must.
func TestMediaRefusesJunctionEscapes(t *testing.T) {
	s, home, proj, _ := newMediaTestServer(t)
	outside := filepath.Join(home, "private")
	if err := os.MkdirAll(outside, 0o700); err != nil {
		t.Fatal(err)
	}
	png := []byte("\x89PNG\r\n\x1a\n")
	if err := os.WriteFile(filepath.Join(outside, "pic.png"), png, 0o600); err != nil {
		t.Fatal(err)
	}
	away := filepath.Join(proj, "assets", "away")
	if out, err := exec.Command("cmd", "/c", "mklink", "/J", away, outside).CombinedOutput(); err != nil {
		t.Skipf("junctions unavailable: %v: %s", err, out)
	}
	code, body := doWorkspaceReq(t, s, http.MethodGet, "/api/media?path="+url.QueryEscape(filepath.Join(away, "pic.png")))
	if code != http.StatusForbidden {
		t.Fatalf("junction out of the roots: status=%d body=%s", code, body)
	}

	// A junction that stays inside the roots still serves.
	inside := filepath.Join(proj, "assets", "real")
	if err := os.MkdirAll(inside, 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(inside, "pic.png"), png, 0o600); err != nil {
		t.Fatal(err)
	}
	alias := filepath.Join(proj, "assets", "alias")
	if out, err := exec.Command("cmd", "/c", "mklink", "/J", alias, inside).CombinedOutput(); err != nil {
		t.Fatalf("mklink: %v: %s", err, out)
	}
	if code, body := doWorkspaceReq(t, s, http.MethodGet, "/api/media?path="+url.QueryEscape(filepath.Join(alias, "pic.png"))); code != http.StatusOK {
		t.Fatalf("in-root junction: status=%d body=%s", code, body)
	}
}
