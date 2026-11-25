package httpapi

// Native (Python-free) VisionWorker: catalog, progress, download/resume/verify,
// install, uninstall, bundle activation. Server process lives in
// vision_native_server.go, archive extraction in vision_native_extract.go.
// Spec: src/remedy/vision/{install,service,progress,config}.py and
// src/remedy/runtime/{catalog,bundle}.py.

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"errors"
	"fmt"
	"io"
	"math"
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/sysres"
)

// Overridable by tests (httptest.Server stands in for both hosts).
var (
	nativeVisionHFBase = "https://huggingface.co"
	nativeVisionGHBase = "https://github.com"
	// nativeVisionServerBinary finds llama-server under the Remedy home.
	nativeVisionServerBinary = visionRuntimeBinary
)

var errNativeVisionCancelled = errors.New("Install cancelled by user")

// nvAsset is one downloadable file. SHA256 "" means the catalog has no hash yet
// (Python "TODO"): only the size is checked, exactly like the Python installer.
type nvAsset struct {
	Name   string
	URL    string
	Size   int64
	SHA256 string
}

// nvRuntime is one pinned llama.cpp package (remedy.runtime.catalog.LLAMA_RUNTIMES).
type nvRuntime struct {
	ID, Platform, Tag, ZipName string
	Size                       int64
	SHA256                     string
	Extra                      []nvAsset // GitHub release assets (cudart); URL filled by ghURL
}

func nvGHURL(tag, name string) string {
	return nativeVisionGHBase + "/ggml-org/llama.cpp/releases/download/" + tag + "/" + name
}

// Copied from remedy.runtime.catalog.LLAMA_RUNTIMES (tag b12628).
func nvDefaultRuntimes() map[string]nvRuntime {
	return map[string]nvRuntime{
		"win-cpu-x64": {ID: "win-cpu-x64", Platform: "win-cpu-x64", Tag: visionLlamaCPPTag,
			ZipName: "llama-b12628-bin-win-cpu-x64.zip", Size: 25_037_881,
			SHA256: "e2f60be1e39ae47743e89f6210886e8d985b67108e1be19b7bcc166d863bc580"},
		"win-cuda-12.4-x64": {ID: "win-cuda-12.4-x64", Platform: "win-cuda-12.4-x64", Tag: visionLlamaCPPTag,
			ZipName: "llama-b12628-bin-win-cuda-12.4-x64.zip", Size: 357_973_598,
			SHA256: "dfb2574d6d5ea69d79b1ff69331b9e85b76bb6920278d3f0193b1586276f3bbd",
			Extra: []nvAsset{{Name: "cudart-llama-bin-win-cuda-12.4-x64.zip", Size: 391_443_627,
				SHA256: "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6"}}},
		"linux-cpu-x64": {ID: "linux-cpu-x64", Platform: "linux-cpu-x64", Tag: visionLlamaCPPTag,
			ZipName: "llama-b12628-bin-ubuntu-x64.tar.gz", Size: 23_225_939,
			SHA256: "d84ba76c8a9066f13f55834f0171cfa9bae86e228e66f9b2bbff9769d7cbf389"},
		"linux-vulkan-x64": {ID: "linux-vulkan-x64", Platform: "linux-vulkan-x64", Tag: visionLlamaCPPTag,
			ZipName: "llama-b12628-bin-ubuntu-vulkan-x64.tar.gz", Size: 36_908_581,
			SHA256: "35f49f4c3f7d66619c596f3f0b55fa73451484a03959680e37c08636e2e8c363"},
	}
}

func (r nvRuntime) assets() []nvAsset {
	out := []nvAsset{{Name: r.ZipName, URL: nvGHURL(r.Tag, r.ZipName), Size: r.Size, SHA256: r.SHA256}}
	for _, a := range r.Extra {
		a.URL = nvGHURL(r.Tag, a.Name)
		out = append(out, a)
	}
	return out
}

func nvModelAssets(m visionModelSpec) []nvAsset {
	base := nativeVisionHFBase + "/" + m.HFRepo + "/resolve/main/"
	usable := func(s string) string {
		if s = strings.TrimSpace(s); strings.EqualFold(s, "todo") {
			return ""
		}
		return s
	}
	return []nvAsset{
		{Name: m.ModelFile, URL: base + m.ModelFile, Size: m.ModelBytes, SHA256: usable(m.ModelSHA256)},
		{Name: m.MMProjFile, URL: base + m.MMProjFile, Size: m.MMProjBytes, SHA256: usable(m.MMProjSHA256)},
	}
}

func nvPlatformMatchesHost(p string) bool {
	p = strings.ToLower(p)
	switch runtime.GOOS {
	case "windows":
		return strings.HasPrefix(p, "win")
	case "linux":
		return strings.HasPrefix(p, "linux")
	}
	return false
}

// nvNormalizeRuntime mirrors normalize_runtime_id.
func (w *nativeVisionWorker) normalizeRuntime(id string, preferGPU bool) string {
	id = strings.TrimSpace(id)
	want := preferGPU
	if r, ok := w.runtimes[id]; ok {
		if nvPlatformMatchesHost(r.Platform) {
			return id
		}
		p := strings.ToLower(r.Platform)
		want = want || strings.Contains(p, "cuda") || strings.Contains(p, "vulkan")
	}
	return visionDefaultRuntime(want)
}

// model resolves an id like get_model_spec; unknown ids report !ok.
func (w *nativeVisionWorker) model(id string) (visionModelSpec, bool) {
	id = strings.TrimSpace(id)
	if id == "" || id == "smolvlm2-2.2b" || id == "qwen2.5-vl-3b" {
		id = visionDefaultModelID
	}
	for _, m := range w.models {
		if m.ID == id {
			return m, true
		}
	}
	return visionModelSpec{}, false
}

func (w *nativeVisionWorker) installBytes(m visionModelSpec, r nvRuntime) int64 {
	total := m.approxDownloadBytes() + r.Size
	for _, a := range r.Extra {
		total += a.Size
	}
	return total
}

// ---- progress (remedy.vision.progress, process-wide state under one mutex) --

type nvProgress struct {
	mu    sync.Mutex
	s     map[string]any
	trail []string // phases in the order they were entered (for tests)
}

func nvIdleProgress() map[string]any {
	return map[string]any{
		"phase": "idle", "message": "", "bytes_done": int64(0), "bytes_total": int64(0),
		"current_file": "", "error": nil, "started_at": nil, "finished_at": nil,
		"model_id": nil, "runtime_id": nil, "cancellable": false, "resumed": false,
	}
}

func (p *nvProgress) snapshot() map[string]any {
	p.mu.Lock()
	defer p.mu.Unlock()
	out := make(map[string]any, 12)
	if p.s == nil {
		return nvIdleProgress()
	}
	for k, v := range p.s {
		out[k] = v
	}
	return out
}

func (p *nvProgress) update(kv map[string]any) {
	p.mu.Lock()
	defer p.mu.Unlock()
	if p.s == nil {
		p.s = nvIdleProgress()
	}
	for k, v := range kv {
		p.s[k] = v
	}
	if ph, ok := kv["phase"].(string); ok && (len(p.trail) == 0 || p.trail[len(p.trail)-1] != ph) {
		p.trail = append(p.trail, ph)
	}
}

func (p *nvProgress) reset() {
	p.mu.Lock()
	p.s = nvIdleProgress()
	p.mu.Unlock()
}

func nvNow() float64 { return float64(time.Now().UnixNano()) / 1e9 }

func (p *nvProgress) begin(modelID, runtimeID string, total, done int64, resumed bool) {
	msg := "Starting install…"
	if resumed {
		msg = "Resuming install…"
	}
	p.update(map[string]any{
		"phase": "downloading", "message": msg, "bytes_done": done, "bytes_total": total,
		"current_file": "", "error": nil, "started_at": nvNow(), "finished_at": nil,
		"model_id": modelID, "runtime_id": runtimeID, "cancellable": true, "resumed": resumed,
	})
}

func (p *nvProgress) fail(msg string) {
	p.update(map[string]any{"phase": "error", "error": msg, "message": msg,
		"finished_at": nvNow(), "cancellable": false})
}

func (p *nvProgress) cancelled() {
	p.update(map[string]any{"phase": "cancelled", "error": nil,
		"message":     "Install cancelled — partial downloads kept for resume",
		"finished_at": nvNow(), "cancellable": false})
}

func (p *nvProgress) succeed(msg string) {
	p.update(map[string]any{"phase": "ready", "message": msg, "error": nil,
		"finished_at": nvNow(), "cancellable": false})
}

func (p *nvProgress) installing() bool {
	switch p.snapshot()["phase"] {
	case "downloading", "extracting", "verifying", "uninstalling":
		return true
	}
	return false
}

// ---- worker -----------------------------------------------------------------

// nativeVisionWorker implements VisionWorker without Python.
type nativeVisionWorker struct {
	prog nvProgress

	models   []visionModelSpec
	runtimes map[string]nvRuntime
	client   *http.Client

	mu     sync.Mutex // guards the install goroutine handle
	cancel context.CancelFunc
	done   chan struct{}

	procMu  sync.Mutex // guards proc
	proc    *visionProc
	startMu sync.Mutex // serialises Start

	// Idle auto-stop (vision.runtime.mark_used / maybe_idle_stop).
	now       func() time.Time // injectable clock
	idleEvery time.Duration    // watcher period (Python: 30 s)
	idleMu    sync.Mutex
	lastUsed  time.Time
	idleHome  string
	idleQuit  chan struct{}
}

func newNativeVisionWorker() *nativeVisionWorker {
	return &nativeVisionWorker{
		now: time.Now, idleEvery: 30 * time.Second,
		models:   append([]visionModelSpec(nil), visionModels...),
		runtimes: nvDefaultRuntimes(),
		// urllib follows up to 10 redirects; so does net/http. The socket
		// timeout (120 s) becomes a response-header timeout.
		client: &http.Client{Transport: &http.Transport{
			Proxy: http.ProxyFromEnvironment, ResponseHeaderTimeout: 120 * time.Second,
		}},
	}
}

var _ VisionWorker = (*nativeVisionWorker)(nil)

func (w *nativeVisionWorker) Progress(_ context.Context, _ string) map[string]any {
	return w.prog.snapshot()
}

func (w *nativeVisionWorker) installRunning() bool {
	w.mu.Lock()
	defer w.mu.Unlock()
	if w.done == nil {
		return false
	}
	select {
	case <-w.done:
		return false
	default:
		return true
	}
}

func (w *nativeVisionWorker) vcfg(home string) map[string]any {
	return visionSectionFromConfig(LoadConfig(home))
}

func (w *nativeVisionWorker) CancelInstall(_ context.Context, _ string) (map[string]any, error) {
	w.mu.Lock()
	cancel := w.cancel
	w.mu.Unlock()
	if !w.prog.installing() && !w.installRunning() {
		// Python spreads the snapshot last, which blanks "error"; the message wins here.
		return nvMerge(w.prog.snapshot(), map[string]any{"ok": false, "error": "No install in progress"}), nil
	}
	if cancel != nil {
		cancel()
	}
	w.prog.update(map[string]any{"message": "Cancelling…"})
	return nvMerge(map[string]any{"ok": true, "message": "Cancel requested"}, w.prog.snapshot()), nil
}

func nvMerge(base map[string]any, more map[string]any) map[string]any {
	for k, v := range more {
		base[k] = v
	}
	return base
}

// ---- downloads --------------------------------------------------------------

func nvSHA256File(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	h := sha256.New()
	if _, err := io.Copy(h, f); err != nil {
		return "", err
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

func nvAssetComplete(path string, a nvAsset) bool {
	st, err := os.Stat(path)
	if err != nil || st.IsDir() {
		return false
	}
	if a.Size > 0 && st.Size() != a.Size {
		return false
	}
	if a.SHA256 != "" {
		got, err := nvSHA256File(path)
		return err == nil && strings.EqualFold(got, a.SHA256)
	}
	return true
}

func nvPartial(dest string) string { return dest + ".partial" }

type nvHTTPError struct {
	code int
	text string
}

func (e *nvHTTPError) Error() string { return fmt.Sprintf("HTTP Error %d: %s", e.code, e.text) }

// downloadAsset fetches a into dest, resuming from dest+".partial" with HTTP
// Range. Nothing is renamed into place unless size and (when known) SHA-256
// match. onChunk gets byte counts; onRewind gets bytes thrown away on restart.
func (w *nativeVisionWorker) downloadAsset(ctx context.Context, a nvAsset, dest string,
	onChunk func(int), onRewind func(int64)) error {
	if err := os.MkdirAll(filepath.Dir(dest), 0o755); err != nil {
		return err
	}
	if nvAssetComplete(dest, a) {
		return nil
	}
	partial := nvPartial(dest)
	var existing int64
	if st, err := os.Stat(partial); err == nil && st.Mode().IsRegular() {
		existing = st.Size()
	}
	if existing > 0 && a.Size > 0 && existing > a.Size {
		_ = os.Remove(partial)
		existing = 0
	}
	restart := func() {
		if onRewind != nil && existing > 0 {
			onRewind(existing)
		}
		_ = os.Remove(partial)
		existing = 0
	}
	stream := func(resume bool) error {
		req, err := http.NewRequestWithContext(ctx, http.MethodGet, a.URL, nil)
		if err != nil {
			return err
		}
		req.Header.Set("User-Agent", "RemedyAI-vision/1.0")
		if resume && existing > 0 {
			req.Header.Set("Range", fmt.Sprintf("bytes=%d-", existing))
		}
		resp, err := w.client.Do(req)
		if err != nil {
			return err
		}
		defer resp.Body.Close()
		if resp.StatusCode < 200 || resp.StatusCode > 299 {
			return &nvHTTPError{resp.StatusCode, http.StatusText(resp.StatusCode)}
		}
		appendMode := resume && existing > 0
		if appendMode {
			// Range ignored (200), or answered from another offset: rewrite.
			start := int64(-1)
			if resp.StatusCode == http.StatusPartialContent {
				if cr := resp.Header.Get("Content-Range"); strings.HasPrefix(cr, "bytes ") {
					if n, err := strconv.ParseInt(strings.SplitN(strings.TrimPrefix(cr, "bytes "), "-", 2)[0], 10, 64); err == nil {
						start = n
					}
				}
			}
			if resp.StatusCode == http.StatusOK || (start >= 0 && start != existing) {
				restart()
				appendMode = false
			}
		}
		flags := os.O_WRONLY | os.O_CREATE | os.O_TRUNC
		if appendMode {
			flags = os.O_WRONLY | os.O_CREATE | os.O_APPEND
		}
		out, err := os.OpenFile(partial, flags, 0o644)
		if err != nil {
			return err
		}
		defer out.Close()
		buf := make([]byte, 256*1024)
		for {
			if ctx.Err() != nil {
				return errNativeVisionCancelled
			}
			n, rerr := resp.Body.Read(buf)
			if n > 0 {
				if _, err := out.Write(buf[:n]); err != nil {
					return err
				}
				if onChunk != nil {
					onChunk(n)
				}
			}
			if rerr == io.EOF {
				return nil
			}
			if rerr != nil {
				if ctx.Err() != nil {
					return errNativeVisionCancelled
				}
				return rerr
			}
		}
	}

	err := stream(existing > 0)
	var he *nvHTTPError
	if errors.As(err, &he) && existing > 0 && (he.code == 400 || he.code == 416 || he.code == 501) {
		restart() // server cannot resume: start over
		err = stream(false)
	}
	if err != nil {
		if ctx.Err() != nil {
			return errNativeVisionCancelled
		}
		return err
	}
	if ctx.Err() != nil {
		return errNativeVisionCancelled
	}
	st, err := os.Stat(partial)
	if err != nil {
		return fmt.Errorf("Download produced no file for %s", a.Name)
	}
	got := st.Size()
	if a.Size > 0 && math.Abs(float64(got-a.Size)) > math.Max(1024*1024, float64(a.Size)*0.02) {
		if got == 0 {
			_ = os.Remove(partial)
		}
		return fmt.Errorf("Size mismatch for %s: expected %d, got %d", a.Name, a.Size, got)
	}
	if a.SHA256 != "" {
		digest, err := nvSHA256File(partial)
		if err != nil {
			return err
		}
		if !strings.EqualFold(digest, a.SHA256) {
			_ = os.Remove(partial)
			return fmt.Errorf("SHA256 mismatch for %s: expected %s, got %s", a.Name, a.SHA256, digest)
		}
	}
	return os.Rename(partial, dest)
}

// existingProgressBytes sums complete or partial bytes already on disk.
func (w *nativeVisionWorker) existingProgressBytes(home string, m visionModelSpec, r nvRuntime) (int64, bool) {
	var total int64
	any := false
	count := func(dir string, a nvAsset) {
		full := filepath.Join(dir, a.Name)
		if nvAssetComplete(full, a) {
			if a.Size > 0 {
				total += a.Size
			} else if st, err := os.Stat(full); err == nil {
				total += st.Size()
			}
		} else if st, err := os.Stat(nvPartial(full)); err == nil {
			total += st.Size()
			any = true
		}
	}
	for _, a := range r.assets() {
		count(nvDownloadsDir(home), a)
	}
	for _, a := range nvModelAssets(m) {
		count(visionModelsDir(home, m.ID), a)
	}
	return total, any
}

func nvDownloadsDir(home string) string { return filepath.Join(visionHome(home), "downloads") }

// ---- install ----------------------------------------------------------------

func (w *nativeVisionWorker) runInstall(ctx context.Context, home string, m visionModelSpec, r nvRuntime, enable bool) {
	err := w.doInstall(ctx, home, m, r, enable)
	switch {
	case err == nil:
	case errors.Is(err, errNativeVisionCancelled) || ctx.Err() != nil:
		w.prog.cancelled()
	default:
		w.prog.fail(err.Error())
	}
}

func (w *nativeVisionWorker) doInstall(ctx context.Context, home string, m visionModelSpec, r nvRuntime, enable bool) error {
	total := w.installBytes(m, r)
	already, resumed := w.existingProgressBytes(home, m, r)
	w.prog.begin(m.ID, r.ID, total, already, resumed)
	var mu sync.Mutex
	done := already
	bump := func(n int) {
		mu.Lock()
		done += int64(n)
		d := done
		mu.Unlock()
		cur, _ := w.prog.snapshot()["current_file"].(string)
		if cur == "" {
			cur = "…"
		}
		w.prog.update(map[string]any{"bytes_done": min(d, total), "message": "Downloading " + cur})
	}
	rewind := func(n int64) { mu.Lock(); done -= n; mu.Unlock() }

	dl := nvDownloadsDir(home)
	rdir := visionRuntimeDir(home)
	mdir := visionModelsDir(home, m.ID)
	if err := os.MkdirAll(dl, 0o755); err != nil {
		return err
	}
	if err := os.MkdirAll(mdir, 0o755); err != nil {
		return err
	}
	for _, a := range r.assets() {
		if ctx.Err() != nil {
			return errNativeVisionCancelled
		}
		w.prog.update(map[string]any{"current_file": a.Name, "phase": "downloading"})
		zp := filepath.Join(dl, a.Name)
		if !nvAssetComplete(zp, a) {
			if err := w.downloadAsset(ctx, a, zp, bump, rewind); err != nil {
				return err
			}
		}
		if ctx.Err() != nil {
			return errNativeVisionCancelled
		}
		w.prog.update(map[string]any{"phase": "extracting", "message": "Extracting " + a.Name})
		if err := nvExtractArchive(ctx, zp, rdir); err != nil {
			return err
		}
	}
	if nativeVisionServerBinary(home) == "" {
		return fmt.Errorf("llama-server binary not found after extract. Checked under %s", rdir)
	}
	for _, a := range nvModelAssets(m) {
		if ctx.Err() != nil {
			return errNativeVisionCancelled
		}
		w.prog.update(map[string]any{"phase": "downloading", "current_file": a.Name,
			"message": "Downloading " + a.Name})
		dest := filepath.Join(mdir, a.Name)
		if nvAssetComplete(dest, a) {
			continue
		}
		if err := w.downloadAsset(ctx, a, dest, bump, rewind); err != nil {
			return err
		}
	}
	if ctx.Err() != nil {
		return errNativeVisionCancelled
	}
	w.prog.update(map[string]any{"phase": "verifying", "message": "Writing vision.json", "cancellable": false})
	abs := func(p string) string {
		if a, err := filepath.Abs(p); err == nil {
			return a
		}
		return p
	}
	if err := writeJSONAtomic(visionJSONPath(home), map[string]any{
		"enabled": enable, "backend": "llama_server", "model_id": m.ID,
		"model_path":      abs(filepath.Join(mdir, m.ModelFile)),
		"mmproj_path":     abs(filepath.Join(mdir, m.MMProjFile)),
		"runtime_id":      r.ID,
		"runtime_version": r.Tag,
		"runtime_binary":  nativeVisionServerBinary(home),
		"host":            visionDefaultHost, "port": visionDefaultPort,
		"base_url":   fmt.Sprintf("http://%s:%d/v1", visionDefaultHost, visionDefaultPort),
		"auto_start": true,
	}); err != nil {
		return err
	}
	w.prog.update(map[string]any{"bytes_done": total})
	w.prog.succeed(fmt.Sprintf("Installed %s (llama.cpp %s) — starts with Remedy", m.Name, r.Tag))
	if enable && !nvVisionSuspended(home) {
		w.prog.update(map[string]any{"message": "Starting local model server…"})
		// Failure is not an install failure: the next launch retries.
		_, _ = w.startServer(context.Background(), home, -1, 90*time.Second)
	}
	return nil
}

func (w *nativeVisionWorker) Install(_ context.Context, homeDir, modelID, runtimeID string, preferCUDA bool) (map[string]any, error) {
	home := ResolveHomeDir(homeDir)
	vc := w.vcfg(home)
	mid := strings.TrimSpace(modelID)
	if mid == "" {
		mid = anyString(vc["model_id"])
	}
	spec, ok := w.model(mid)
	if !ok {
		spec, _ = w.model(visionDefaultModelID)
	}
	rid := strings.TrimSpace(runtimeID)
	if rid == "" {
		rid = anyString(vc["runtime_id"])
	}
	if rid == "" && preferCUDA {
		rid = visionDefaultRuntime(true)
	}

	// Already on disk: activate + start, no download.
	if w.isInstalled(home, spec) {
		act := w.activateBundle(home, true, true)
		act["mode"] = "already_installed"
		if anyString(act["message"]) == "" {
			act["message"] = "Local model already present — activated and starting with Remedy."
		}
		return act, nil
	}
	if act := w.activateBundle(home, true, true); anyBoolDef(act["ok"], false) && w.isInstalled(home, spec) {
		act["mode"] = "already_installed"
		return act, nil
	}

	rid = w.normalizeRuntime(rid, preferCUDA)
	rt, ok := w.runtimes[rid]
	if !ok {
		return nil, fmt.Errorf("Unknown runtime %q.", rid)
	}
	health := w.systemHealth(home, spec, rid)
	res := w.startInstall(home, spec, rt, true)
	res["health"] = health
	res["warnings"] = health["warnings"]
	res["mode"] = "download"
	res["message"] = "Downloading pinned " + spec.Name + " + llama-server (same files on every PC). " +
		"Server starts automatically when install finishes."
	return res, nil
}

// startInstall launches the background install (install.start_install).
func (w *nativeVisionWorker) startInstall(home string, m visionModelSpec, r nvRuntime, enable bool) map[string]any {
	w.mu.Lock()
	if w.prog.installing() && w.done != nil {
		select {
		case <-w.done:
		default:
			w.mu.Unlock()
			return nvMerge(w.prog.snapshot(), map[string]any{"ok": false, "error": "Install already in progress"})
		}
	}
	// The install outlives the HTTP request, so it gets its own context.
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan struct{})
	w.cancel, w.done = cancel, done
	w.mu.Unlock()
	w.prog.begin(m.ID, r.ID, w.installBytes(m, r), 0, false)
	go func() {
		defer close(done)
		defer cancel()
		w.runInstall(ctx, home, m, r, enable)
	}()
	return nvMerge(map[string]any{"ok": true}, w.prog.snapshot())
}

func (w *nativeVisionWorker) ReinstallRuntime(_ context.Context, homeDir string, preferCUDA bool) (map[string]any, error) {
	home := ResolveHomeDir(homeDir)
	vc := w.vcfg(home)
	spec, ok := w.model(anyString(vc["model_id"]))
	if !ok {
		spec, _ = w.model(visionDefaultModelID)
	}
	if w.prog.installing() {
		return nvMerge(w.prog.snapshot(), map[string]any{"ok": false, "error": "Install already in progress"}), nil
	}
	_, _ = w.stopServer(home)
	_ = os.RemoveAll(visionRuntimeDir(home))
	rid := w.normalizeRuntime("", preferCUDA)
	rt, ok := w.runtimes[rid]
	if !ok {
		return nil, fmt.Errorf("Unknown runtime %q.", rid)
	}
	// Drop matching archives so the runtime is always fetched fresh.
	for _, a := range rt.assets() {
		z := filepath.Join(nvDownloadsDir(home), a.Name)
		_ = os.Remove(z)
		_ = os.Remove(nvPartial(z))
	}
	res := w.startInstall(home, spec, rt, true)
	if FindConfigPath(home) != "" { // Python only persists into an existing config
		patchVisionConfigEnabled(home, true, spec.ID, visionDefaultRuntime(preferCUDA))
	}
	health := w.systemHealth(home, spec, visionDefaultRuntime(preferCUDA))
	res["health"] = health
	res["warnings"] = health["warnings"]
	return res, nil
}

func (w *nativeVisionWorker) Uninstall(_ context.Context, homeDir string, keepModels bool) (map[string]any, error) {
	home := ResolveHomeDir(homeDir)
	if w.prog.installing() || w.installRunning() {
		w.mu.Lock()
		cancel, done := w.cancel, w.done
		w.mu.Unlock()
		if cancel != nil {
			cancel()
		}
		// Python lets the thread race the wipe; wait briefly so it cannot
		// rewrite files or progress afterwards.
		if done != nil {
			select {
			case <-done:
			case <-time.After(15 * time.Second):
			}
		}
	}
	w.prog.update(map[string]any{"phase": "uninstalling", "message": "Stopping server…", "cancellable": false})
	_, _ = w.stopServer(home)
	root := visionHome(home)
	if _, err := os.Stat(root); err != nil {
		w.prog.reset()
		return map[string]any{"ok": true, "removed": false}, nil
	}
	if keepModels {
		_ = os.RemoveAll(visionRuntimeDir(home))
		_ = os.Remove(visionJSONPath(home))
		_ = os.RemoveAll(nvDownloadsDir(home))
	} else {
		_ = os.RemoveAll(root)
	}
	w.prog.reset()
	return map[string]any{"ok": true, "removed": true, "keep_models": keepModels}, nil
}

// ---- health hints (vision.health.system_health) -----------------------------

func nvNvidiaPresent() bool {
	paths := []string{"/usr/bin/nvidia-smi", "/usr/local/bin/nvidia-smi"}
	if runtime.GOOS == "windows" {
		paths = []string{`C:\Windows\System32\nvidia-smi.exe`, `C:\Program Files\NVIDIA Corporation\NVSMI\nvidia-smi.exe`}
	}
	for _, p := range paths {
		if fileExists(p) {
			return true
		}
	}
	return false
}

func nvRound2(v float64) float64 { return math.Round(v*100) / 100 }

func (w *nativeVisionWorker) systemHealth(_ string, m visionModelSpec, rid string) map[string]any {
	rt := w.runtimes[w.normalizeRuntime(rid, false)]
	need := w.installBytes(m, rt)
	nvidia := nvNvidiaPresent()
	isCPU := strings.Contains(strings.ToLower(rt.Platform), "cpu")
	warnings := []any{}
	var ramBytes any
	var ramGB any
	if snap := sysres.Read(); snap.Known() {
		ramBytes = int64(snap.TotalBytes)
		gb := float64(snap.TotalBytes) / (1 << 30)
		ramGB = nvRound2(gb)
		if gb+0.05 < float64(m.MinRAMGB) {
			warnings = append(warnings, fmt.Sprintf("This PC has ~%.1f GB RAM; the decoder recommends ≥%d GB. "+
				"Decode may be slow or fail under memory pressure.", gb, m.MinRAMGB))
		}
	}
	if isCPU {
		warnings = append(warnings, "CPU runtime selected — visual decode will be slower than a GPU build. "+
			"Fine for occasional screenshots; GPU is better for heavy use.")
		if nvidia {
			warnings = append(warnings, "A GPU is present on this PC. Use a runtime that matches this card "+
				"when you want faster decode.")
		}
	} else if !nvidia {
		warnings = append(warnings, "This GPU runtime may not match the card on this PC. "+
			"If start fails, switch to the CPU runtime.")
	}
	vendor := ""
	if nvidia {
		vendor = "nvidia"
	}
	return map[string]any{
		"ram_bytes": ramBytes, "ram_gb": ramGB,
		"disk_free_bytes": nil, "disk_free_gb": nil, // no free-space API in the allowed imports
		"install_need_bytes": need, "install_need_gb": nvRound2(float64(need) / (1 << 30)),
		"min_ram_gb": m.MinRAMGB, "nvidia_detected": nvidia, "gpu_vendor": vendor,
		"gpu_name": "", "gpu_devices": []any{}, "runtime_id": rid,
		"runtime_platform": rt.Platform, "cpu_runtime": isCPU, "warnings": warnings,
	}
}
