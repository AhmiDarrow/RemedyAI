package httpapi

import (
	"archive/tar"
	"bytes"
	"compress/gzip"
	"crypto/sha256"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync/atomic"
	"testing"
	"time"
)

func openserpArchive(t *testing.T, files map[string]string) []byte {
	t.Helper()
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	for name, body := range files {
		if err := tw.WriteHeader(&tar.Header{Name: name, Mode: 0o755, Size: int64(len(body)), Typeflag: tar.TypeReg}); err != nil {
			t.Fatal(err)
		}
		if _, err := tw.Write([]byte(body)); err != nil {
			t.Fatal(err)
		}
	}
	_ = tw.Close()
	_ = gz.Close()
	return buf.Bytes()
}

// stubSearchHost swaps the network, the spawn and the health probe.
func stubSearchHost(t *testing.T, archive []byte, sum string) (spawned *atomic.Int32, healthy *atomic.Bool) {
	t.Helper()
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if !strings.HasSuffix(r.URL.Path, ".tgz") {
			http.NotFound(w, r)
			return
		}
		_, _ = w.Write(archive)
	}))
	t.Cleanup(srv.Close)
	key := runtime.GOOS + "/" + runtime.GOARCH
	oldBase, oldSum, hadSum := openserpReleaseBase, openserpAssets[key], false
	_, hadSum = openserpAssets[key]
	oldSpawn, oldHealthy := searchHostSpawn, searchHostHealthy
	openserpReleaseBase = srv.URL
	openserpAssets[key] = sum
	spawned, healthy = &atomic.Int32{}, &atomic.Bool{}
	searchHostSpawn = func(_ string, argv []string, _ string) (func() error, error) {
		if len(argv) != 6 || argv[1] != "serve" || argv[3] != "127.0.0.1" {
			t.Errorf("argv=%v", argv)
		}
		spawned.Add(1)
		healthy.Store(true)
		return func() error { healthy.Store(false); return nil }, nil
	}
	searchHostHealthy = healthy.Load
	t.Cleanup(func() {
		openserpReleaseBase, searchHostSpawn, searchHostHealthy = oldBase, oldSpawn, oldHealthy
		if hadSum {
			openserpAssets[key] = oldSum
		} else {
			delete(openserpAssets, key)
		}
	})
	return spawned, healthy
}

func TestSearchHostDownloadsVerifiesAndStarts(t *testing.T) {
	archive := openserpArchive(t, map[string]string{
		"openserp-x/README.md":            "not the binary",
		"openserp-x/" + openserpExeName(): "engine",
		"../../escape.txt":                "must not be written",
	})
	sum := sha256.Sum256(archive)
	spawned, healthy := stubSearchHost(t, archive, hex.EncodeToString(sum[:]))
	home := t.TempDir()
	h := &searchHost{home: home}

	if err := h.ensure(); err != nil {
		t.Fatal(err)
	}
	if got, err := os.ReadFile(h.binary()); err != nil || string(got) != "engine" {
		t.Fatalf("binary=%q err=%v", got, err)
	}
	if _, err := os.Stat(filepath.Join(filepath.Dir(home), "escape.txt")); err == nil {
		t.Fatal("an archive member was written outside the bin folder")
	}
	ents, _ := os.ReadDir(filepath.Join(home, "bin"))
	if len(ents) != 1 {
		t.Fatalf("bin holds more than the engine: %v", ents)
	}
	if spawned.Load() != 1 || !healthy.Load() {
		t.Fatalf("spawned=%d healthy=%v", spawned.Load(), healthy.Load())
	}
	// Already answering: nothing is started twice.
	if err := h.ensure(); err != nil || spawned.Load() != 1 {
		t.Fatalf("second ensure: err=%v spawned=%d", err, spawned.Load())
	}
	h.Stop()
	if healthy.Load() {
		t.Fatal("Stop left the engine running")
	}
}

func TestSearchHostRefusesAWrongDownload(t *testing.T) {
	archive := openserpArchive(t, map[string]string{openserpExeName(): "engine"})
	spawned, _ := stubSearchHost(t, archive, strings.Repeat("0", 64))
	h := &searchHost{home: t.TempDir()}
	err := h.ensure()
	if err == nil || !strings.Contains(err.Error(), "checksum mismatch") {
		t.Fatalf("err=%v", err)
	}
	if _, statErr := os.Stat(h.binary()); statErr == nil {
		t.Fatal("an unverified binary was installed")
	}
	if spawned.Load() != 0 {
		t.Fatal("an unverified binary was started")
	}
}

func TestSearchHostFollowsTheWebToolsSetting(t *testing.T) {
	archive := openserpArchive(t, map[string]string{openserpExeName(): "engine"})
	sum := sha256.Sum256(archive)
	spawned, healthy := stubSearchHost(t, archive, hex.EncodeToString(sum[:]))
	home := t.TempDir()
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("web_tools_enabled = false\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	h := &searchHost{home: home}
	h.Apply()
	time.Sleep(150 * time.Millisecond)
	if spawned.Load() != 0 {
		t.Fatal("the engine started with web tools off")
	}
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte("web_tools_enabled = true\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	h.Apply()
	h.Apply() // a second call while the first works must not start a second engine
	deadline := time.Now().Add(5 * time.Second)
	for !healthy.Load() && time.Now().Before(deadline) {
		time.Sleep(20 * time.Millisecond)
	}
	if spawned.Load() != 1 {
		t.Fatalf("spawned=%d want 1", spawned.Load())
	}
	// A nil host (a server that manages none) does nothing.
	var none *searchHost
	none.Apply()
	none.Stop()
}
