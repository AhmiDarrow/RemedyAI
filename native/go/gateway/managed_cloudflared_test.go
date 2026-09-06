package gateway

import (
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
)

func TestCloudflaredDownloadURLNonEmpty(t *testing.T) {
	url := CloudflaredDownloadURL()
	if url == "" {
		t.Fatal("empty url")
	}
}

func TestLookupManagedCloudflaredEmpty(t *testing.T) {
	home := t.TempDir()
	if got := LookupManagedCloudflared(home); got != "" {
		t.Fatalf("expected empty, got %q", got)
	}
}

func TestLookupManagedCloudflaredFindsBin(t *testing.T) {
	home := t.TempDir()
	dir := ManagedTunnelDir(home)
	binDir := filepath.Join(dir, "bin")
	if err := os.MkdirAll(binDir, 0o700); err != nil {
		t.Fatal(err)
	}
	name := "cloudflared"
	if runtime.GOOS == "windows" {
		name = "cloudflared.exe"
	}
	path := filepath.Join(binDir, name)
	if err := os.WriteFile(path, []byte("fake"), 0o755); err != nil {
		t.Fatal(err)
	}
	got := LookupManagedCloudflared(home)
	if got == "" {
		t.Fatal("expected managed binary")
	}
}

func TestEnsureManagedCloudflaredSkipDownload(t *testing.T) {
	t.Setenv("REMEDY_SKIP_MANAGED_CLOUDFLARED_DOWNLOAD", "1")
	home := t.TempDir()
	if _, err := EnsureManagedCloudflared(home); err == nil {
		t.Fatal("expected error when missing and download skipped")
	}
}

func TestEnsureManagedCloudflaredHTTPtest(t *testing.T) {
	pin, ok := cloudflaredPins[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		t.Skip("no cloudflared pin for this GOOS/GOARCH")
	}
	payload := []byte("remedy-cloudflared-fixture")
	sum := sha256.Sum256(payload)
	wantHex := hex.EncodeToString(sum[:])

	prevDL := managedDownloadFile
	prevBase := cloudflaredDownloadBase
	prevPins := cloudflaredPins
	t.Cleanup(func() {
		managedDownloadFile = prevDL
		cloudflaredDownloadBase = prevBase
		cloudflaredPins = prevPins
	})

	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, "/"+pin.filename) {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write(payload)
	}))
	t.Cleanup(srv.Close)

	cloudflaredDownloadBase = srv.URL + "/"
	cloudflaredPins = map[string]cloudflaredPin{
		runtime.GOOS + "/" + runtime.GOARCH: {
			filename: pin.filename,
			sha256:   wantHex,
			binName:  pin.binName,
		},
	}
	managedDownloadFile = downloadFileSHA256

	home := t.TempDir()
	got, err := EnsureManagedCloudflared(home)
	if err != nil {
		t.Fatalf("ensure: %v", err)
	}
	if got == "" {
		t.Fatal("empty path")
	}
	data, err := os.ReadFile(got)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != string(payload) {
		t.Fatalf("payload mismatch: %q", data)
	}
	// Second call is idempotent.
	got2, err := EnsureManagedCloudflared(home)
	if err != nil || got2 == "" {
		t.Fatalf("second ensure: %v path=%q", err, got2)
	}
}
