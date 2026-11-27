package httpapi

import (
	"archive/tar"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"log"
	"net/http"
	"os"
	"path"
	"path/filepath"
	"runtime"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// The managed local search engine. web.search asks OpenSERP (MIT) on loopback
// before it falls back to DuckDuckGo's HTML page, which answers automated
// reads with a human check. The pinned release is downloaded into
// <home>/bin on first use, verified by SHA-256, bound to 127.0.0.1 on its own
// port and never exposed on the LAN. It runs only while web tools are on.
//
// This was the Python worker's job (runtime/web_search_host.py). Once the
// runtime stopped starting the worker nothing started the engine, and every
// search took the fallback.

const (
	openserpVersion = "0.8.12"
	openserpHost    = "127.0.0.1"
	// Its own port, so a leftover `openserp serve` on 7000 is not ours to take over.
	openserpPort = 17410
	// A failed or dead engine is tried again after this long, not on every search.
	searchHostRetry = 5 * time.Minute
)

// SHA-256 of the official .tgz assets of the pinned release, by GOOS/GOARCH.
var openserpAssets = map[string]string{
	"windows/amd64": "9b93dfdb747641fd8effbbd88fc852d547a0624bf6f391bb65d0d378416e227b",
	"windows/arm64": "0c15b98fa5f558b53daf83226c1e856dc9f020c4b8c79297d228368bfe6953f9",
	"linux/amd64":   "9d1974d885713686d7825cf27c219822bb79445ce30e25ef3f1608652fec781b",
	"linux/arm64":   "ff2b2ebffd771cc72729bec950b031d9bcf68373b7b873e6b7b87211d3bcab62",
}

// Replaced by tests.
var (
	openserpReleaseBase = "https://github.com/karust/openserp/releases/download/v" + openserpVersion
	searchHostSpawn     = zigSearchHostSpawn
	searchHostHealthy   = func() bool {
		c := http.Client{Timeout: 600 * time.Millisecond}
		resp, err := c.Get(fmt.Sprintf("http://%s:%d/health", openserpHost, openserpPort))
		if err != nil {
			return false
		}
		_ = resp.Body.Close()
		return resp.StatusCode < 500
	}
)

type searchHost struct {
	home string

	mu      sync.Mutex
	stop    func() error // nil while this runtime has started no engine
	busy    bool
	lastTry time.Time
	lastErr string
}

// searchHostFor is the host for a server that manages one, else nil (every
// method on a nil host does nothing).
func searchHostFor(cfg Config) *searchHost {
	if !cfg.ManageSearchHost {
		return nil
	}
	return &searchHost{home: ResolveHomeDir(cfg.HomeDir)}
}

func openserpExeName() string {
	if runtime.GOOS == "windows" {
		return "openserp.exe"
	}
	return "openserp"
}

func (h *searchHost) binary() string { return filepath.Join(h.home, "bin", openserpExeName()) }

// Apply brings the engine in line with the web tools setting: started (and
// downloaded first if need be) in the background when they are on, stopped
// when they are off. Safe to call often; a start is tried at most once per
// searchHostRetry.
func (h *searchHost) Apply() {
	if h == nil {
		return
	}
	if !cfgBool(LoadConfig(h.home), "web_tools_enabled", true) {
		h.Stop()
		return
	}
	h.mu.Lock()
	if h.busy || (!h.lastTry.IsZero() && time.Since(h.lastTry) < searchHostRetry) {
		h.mu.Unlock()
		return
	}
	h.busy, h.lastTry = true, time.Now()
	h.mu.Unlock()
	go func() {
		err := h.ensure()
		h.mu.Lock()
		h.busy = false
		h.lastErr = ""
		if err != nil {
			h.lastErr = err.Error()
		}
		h.mu.Unlock()
		if err != nil {
			log.Printf("web search: local engine not started (searches use the fallback): %v", err)
		}
	}()
}

// Stop ends the engine this runtime started. One it did not start is left alone.
func (h *searchHost) Stop() {
	if h == nil {
		return
	}
	h.mu.Lock()
	stop := h.stop
	h.stop = nil
	h.lastTry = time.Time{}
	h.mu.Unlock()
	if stop != nil {
		_ = stop()
	}
}

func (h *searchHost) ensure() error {
	if searchHostHealthy() {
		return nil
	}
	h.mu.Lock()
	stale := h.stop
	h.stop = nil
	h.mu.Unlock()
	if stale != nil {
		_ = stale() // started earlier and no longer answering
	}
	bin := h.binary()
	if st, err := os.Stat(bin); err != nil || st.IsDir() {
		if err := h.download(bin); err != nil {
			return err
		}
	}
	argv := []string{bin, "serve", "-a", openserpHost, "-p", fmt.Sprint(openserpPort)}
	stop, err := searchHostSpawn(h.home, argv, filepath.Dir(bin))
	if err != nil {
		return fmt.Errorf("start: %w", err)
	}
	deadline := time.Now().Add(12 * time.Second)
	for time.Now().Before(deadline) {
		if searchHostHealthy() {
			h.mu.Lock()
			h.stop = stop
			h.mu.Unlock()
			log.Printf("web search: local engine listening on %s:%d", openserpHost, openserpPort)
			return nil
		}
		time.Sleep(200 * time.Millisecond)
	}
	_ = stop()
	return errors.New("the engine did not answer in time")
}

// download fetches the pinned release, checks its hash and writes the binary.
func (h *searchHost) download(dest string) error {
	sum, ok := openserpAssets[runtime.GOOS+"/"+runtime.GOARCH]
	if !ok {
		return fmt.Errorf("no OpenSERP build for %s %s", runtime.GOOS, runtime.GOARCH)
	}
	name := fmt.Sprintf("openserp-%s-%s-%s.tgz", runtime.GOOS, runtime.GOARCH, openserpVersion)
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	tmp, err := os.CreateTemp(filepath.Dir(dest), "openserp-*.tgz")
	if err != nil {
		return err
	}
	defer os.Remove(tmp.Name())
	defer tmp.Close()

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Minute)
	defer cancel()
	req, err := http.NewRequestWithContext(ctx, http.MethodGet, openserpReleaseBase+"/"+name, nil)
	if err != nil {
		return err
	}
	req.Header.Set("User-Agent", "Remedy/"+Version)
	resp, err := http.DefaultClient.Do(req)
	if err != nil {
		return fmt.Errorf("download: %w", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		return fmt.Errorf("download: %s", resp.Status)
	}
	hash := sha256.New()
	// The real archive is about 10 MB; the cap stops a wrong answer filling the disk.
	if _, err := io.Copy(io.MultiWriter(tmp, hash), io.LimitReader(resp.Body, 256<<20)); err != nil {
		return fmt.Errorf("download: %w", err)
	}
	if got := hex.EncodeToString(hash.Sum(nil)); got != sum {
		return fmt.Errorf("checksum mismatch for %s: got %s", name, got)
	}
	if _, err := tmp.Seek(0, io.SeekStart); err != nil {
		return err
	}
	return extractOpenserp(tmp, dest)
}

// extractOpenserp writes the one binary in the archive to dest. Nothing else
// in the archive is written, so its member names cannot place a file anywhere.
func extractOpenserp(archive io.Reader, dest string) error {
	gz, err := gzip.NewReader(archive)
	if err != nil {
		return err
	}
	defer gz.Close()
	tr := tar.NewReader(gz)
	for {
		hdr, err := tr.Next()
		if errors.Is(err, io.EOF) {
			return errors.New("openserp binary not found in the archive")
		}
		if err != nil {
			return err
		}
		base := path.Base(filepath.ToSlash(hdr.Name))
		if hdr.Typeflag != tar.TypeReg || (base != "openserp" && base != "openserp.exe") {
			continue
		}
		part := dest + ".part"
		out, err := os.OpenFile(part, os.O_CREATE|os.O_WRONLY|os.O_TRUNC, 0o755)
		if err != nil {
			return err
		}
		_, err = io.Copy(out, io.LimitReader(tr, 512<<20))
		if cerr := out.Close(); err == nil {
			err = cerr
		}
		if err != nil {
			_ = os.Remove(part)
			return err
		}
		return os.Rename(part, dest)
	}
}

func zigSearchHostSpawn(home string, argv []string, cwd string) (func() error, error) {
	key, err := secret.EnsureHostSigningKey(home)
	if err != nil {
		return nil, err
	}
	if err := core.EnsureSigningKey(key); err != nil {
		return nil, err
	}
	token, nowMS, err := core.IssueProcessSpawnToken(argv, nil, false, false)
	if err != nil {
		return nil, err
	}
	var pid uint32
	var handle uint64
	if err := core.WithWriteJail(nil, func() error {
		var err error
		pid, handle, err = core.ProcessSpawnAuthorized(argv, cwd, nil, false, token, "", "", false, nowMS)
		return err
	}); err != nil {
		return nil, err
	}
	return func() error {
		var joined error
		if pid != 0 {
			joined = errors.Join(joined, core.ProcessKillTree(pid))
		}
		return errors.Join(joined, core.ProcessClose(handle))
	}, nil
}

// bindSearchHost lets web.search ask for the engine when it finds it down.
func bindSearchHost(h *searchHost) {
	if h == nil {
		return
	}
	tools.SetSearchHostNudge(h.Apply)
}
