package httpapi

import (
	"archive/tar"
	"archive/zip"
	"bytes"
	"compress/gzip"
	"context"
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"net"
	"net/http"
	"net/http/httptest"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
	"testing"
	"time"
)

// ---- fixtures -----------------------------------------------------------------

const nvTestModelFile, nvTestMMProjFile = "fake-model.gguf", "fake-mmproj.gguf"

func nvTestBytes(seed byte, n int) []byte {
	b := make([]byte, n)
	for i := range b {
		b[i] = seed + byte(i%251)
	}
	return b
}

func nvTestSum(b []byte) string { s := sha256.Sum256(b); return hex.EncodeToString(s[:]) }

func nvTestBinName() string {
	if runtime.GOOS == "windows" {
		return "llama-server.exe"
	}
	return "llama-server"
}

// nvTestArchive builds the runtime archive for this OS (zip on Windows, tar.gz on Linux).
func nvTestArchive(t *testing.T, files map[string][]byte) (name string, data []byte) {
	t.Helper()
	var buf bytes.Buffer
	if runtime.GOOS == "windows" {
		zw := zip.NewWriter(&buf)
		for n, c := range files {
			f, err := zw.Create(n)
			if err != nil {
				t.Fatal(err)
			}
			f.Write(c)
		}
		zw.Close()
		return "fake-runtime.zip", buf.Bytes()
	}
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	for n, c := range files {
		tw.WriteHeader(&tar.Header{Name: n, Mode: 0o644, Size: int64(len(c)), Typeflag: tar.TypeReg})
		tw.Write(c)
	}
	tw.Close()
	gz.Close()
	return "fake-runtime.tar.gz", buf.Bytes()
}

type nvReq struct{ path, rng, ua string }

type nvFixture struct {
	t        *testing.T
	home     string
	w        *nativeVisionWorker
	srv      *httptest.Server
	model    []byte
	mmproj   []byte
	archive  []byte
	mu       sync.Mutex
	reqs     []nvReq
	hook     func(w http.ResponseWriter, r *http.Request, name string, body []byte) bool // true = handled
	rtName   string
	rtID     string
	modelURL string
}

func (f *nvFixture) requests(suffix string) []nvReq {
	f.mu.Lock()
	defer f.mu.Unlock()
	var out []nvReq
	for _, r := range f.reqs {
		if strings.HasSuffix(r.path, suffix) {
			out = append(out, r)
		}
	}
	return out
}

func newNVFixture(t *testing.T, runtimeFiles map[string][]byte) *nvFixture {
	t.Helper()
	f := &nvFixture{t: t, home: t.TempDir(), model: nvTestBytes(1, 600*1024), mmproj: nvTestBytes(7, 90*1024)}
	t.Setenv("REMEDY_HOME", f.home)
	t.Setenv("REMEDY_LOCAL_BUNDLE", t.TempDir()) // empty: never pick up a real bundle
	if runtimeFiles == nil {
		runtimeFiles = map[string][]byte{nvTestBinName(): []byte("fake llama-server")}
	}
	f.rtName, f.archive = nvTestArchive(t, runtimeFiles)

	f.srv = httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		f.mu.Lock()
		f.reqs = append(f.reqs, nvReq{r.URL.Path, r.Header.Get("Range"), r.Header.Get("User-Agent")})
		f.mu.Unlock()
		name := r.URL.Path[strings.LastIndex(r.URL.Path, "/")+1:]
		var body []byte
		switch name {
		case nvTestModelFile:
			body = f.model
		case nvTestMMProjFile:
			body = f.mmproj
		case f.rtName:
			body = f.archive
		default:
			http.NotFound(w, r)
			return
		}
		if f.hook != nil && f.hook(w, r, name, body) {
			return
		}
		http.ServeContent(w, r, name, time.Time{}, bytes.NewReader(body))
	}))
	t.Cleanup(f.srv.Close)

	oldHF, oldGH, oldSpawn, oldHelp, oldPoll := nativeVisionHFBase, nativeVisionGHBase, nativeVisionSpawn, nativeVisionHelp, nativeVisionPoll
	t.Cleanup(func() {
		nativeVisionHFBase, nativeVisionGHBase, nativeVisionSpawn, nativeVisionHelp, nativeVisionPoll =
			oldHF, oldGH, oldSpawn, oldHelp, oldPoll
	})
	nativeVisionHFBase, nativeVisionGHBase = f.srv.URL, f.srv.URL
	nativeVisionHelp = func(string, string) []byte { return nil }
	nativeVisionPoll = 20 * time.Millisecond
	// Default: starting the server is not what these tests are about.
	nativeVisionSpawn = func(context.Context, string, []string, string) (*visionProc, error) {
		return nil, fmt.Errorf("spawn disabled in test")
	}

	f.w = newNativeVisionWorker()
	f.w.models = []visionModelSpec{{
		ID: visionDefaultModelID, Name: "Fake model", HFRepo: "fake/repo",
		ModelFile: nvTestModelFile, MMProjFile: nvTestMMProjFile,
		ModelSHA256: "TODO", MMProjSHA256: nvTestSum(f.mmproj),
		ModelBytes: int64(len(f.model)), MMProjBytes: int64(len(f.mmproj)),
		MinRAMGB: 1, License: "test", Quant: "Q6_K",
	}}
	f.rtID = visionDefaultRuntime(false)
	f.setRuntime(f.archive)
	return f
}

func (f *nvFixture) setRuntime(archive []byte) {
	f.w.runtimes = map[string]nvRuntime{f.rtID: {
		ID: f.rtID, Platform: f.rtID, Tag: "tfake", ZipName: f.rtName,
		Size: int64(len(archive)), SHA256: nvTestSum(archive),
	}}
}

func (f *nvFixture) installAndWait(prefer bool) map[string]any {
	f.t.Helper()
	res, err := f.w.Install(context.Background(), f.home, "", "", prefer)
	if err != nil {
		f.t.Fatal(err)
	}
	f.wait()
	return res
}

func (f *nvFixture) wait() {
	f.t.Helper()
	f.w.mu.Lock()
	done := f.w.done
	f.w.mu.Unlock()
	if done == nil {
		return
	}
	select {
	case <-done:
	case <-time.After(20 * time.Second):
		f.t.Fatalf("install did not finish; progress=%v", f.w.prog.snapshot())
	}
}

func (f *nvFixture) phase() string { return f.w.prog.snapshot()["phase"].(string) }

func (f *nvFixture) modelPath() string {
	return filepath.Join(visionModelsDir(f.home, visionDefaultModelID), nvTestModelFile)
}

func nvEqualSeq(got, want []string) bool {
	// want must appear in got, in order.
	i := 0
	for _, g := range got {
		if i < len(want) && g == want[i] {
			i++
		}
	}
	return i == len(want)
}

// ---- tests --------------------------------------------------------------------

func TestVisionNativeCatalogMatchesPython(t *testing.T) {
	rts := nvDefaultRuntimes()
	cuda := rts["win-cuda-12.4-x64"]
	if len(rts) != 4 || cuda.Size != 357_973_598 || len(cuda.Extra) != 1 ||
		cuda.Extra[0].SHA256 != "8c79a9b226de4b3cacfd1f83d24f962d0773be79f1e7b75c6af4ded7e32ae1d6" {
		t.Fatalf("runtime catalog drifted: %+v", cuda)
	}
	if rts["linux-cpu-x64"].SHA256 != "d84ba76c8a9066f13f55834f0171cfa9bae86e228e66f9b2bbff9769d7cbf389" {
		t.Fatal("linux-cpu sha drifted")
	}
	a := rts["win-cpu-x64"].assets()[0]
	if a.URL != "https://github.com/ggml-org/llama.cpp/releases/download/b12628/llama-b12628-bin-win-cpu-x64.zip" {
		t.Fatalf("url: %s", a.URL)
	}
	m := nvModelAssets(visionModels[0])
	if m[0].SHA256 != "" || m[0].URL != "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/main/Qwen3.5-4B-Q6_K.gguf" ||
		m[1].SHA256 != "cd88edcf8d031894960bb0c9c5b9b7e1fea6ebee02b9f7ce925a00d12891f864" {
		t.Fatalf("model assets: %+v", m)
	}
	w := newNativeVisionWorker()
	if got := w.installBytes(visionModels[0], rts["win-cuda-12.4-x64"]); got != 3_525_956_768+672_423_616+357_973_598+391_443_627 {
		t.Fatalf("total bytes %d", got)
	}
}

func TestVisionNativeInstallHappyPath(t *testing.T) {
	f := newNVFixture(t, nil)
	res := f.installAndWait(false)
	if res["ok"] != true || res["mode"] != "download" {
		t.Fatalf("install result: %v", res)
	}
	if f.phase() != "ready" {
		t.Fatalf("phase %s: %v", f.phase(), f.w.prog.snapshot())
	}
	f.w.prog.mu.Lock()
	trail := append([]string(nil), f.w.prog.trail...)
	f.w.prog.mu.Unlock()
	if !nvEqualSeq(trail, []string{"downloading", "extracting", "downloading", "verifying", "ready"}) {
		t.Fatalf("phases out of order: %v", trail)
	}
	snap := f.w.prog.snapshot()
	if snap["bytes_done"] != snap["bytes_total"] || snap["cancellable"] != false || snap["error"] != nil {
		t.Fatalf("final progress: %v", snap)
	}
	got, _ := os.ReadFile(f.modelPath())
	if !bytes.Equal(got, f.model) {
		t.Fatal("model content differs")
	}
	if !f.w.isInstalled(f.home, f.w.models[0]) {
		t.Fatal("install not recognised")
	}
	state := loadVisionJSON(f.home)
	if state["model_path"] != f.modelPath() || state["backend"] != "llama_server" ||
		state["runtime_id"] != f.rtID || state["enabled"] != true || anyInt(state["port"], 0) != 8740 ||
		state["runtime_binary"] == "" {
		t.Fatalf("vision.json: %v", state)
	}
	for _, r := range f.requests("") {
		if r.ua != "RemedyAI-vision/1.0" || r.rng != "" {
			t.Fatalf("request %+v", r)
		}
	}
	if len(f.w.Progress(context.Background(), f.home)) < 12 {
		t.Fatal("progress keys missing")
	}
}

func TestVisionNativeResumeAfterDroppedConnection(t *testing.T) {
	f := newNVFixture(t, nil)
	var once sync.Once
	f.hook = func(w http.ResponseWriter, r *http.Request, name string, body []byte) bool {
		drop := false
		if name == nvTestModelFile && r.Header.Get("Range") == "" {
			once.Do(func() { drop = true })
		}
		if !drop {
			return false
		}
		w.Header().Set("Content-Length", strconv.Itoa(len(body)))
		w.WriteHeader(200)
		w.Write(body[:len(body)/2])
		w.(http.Flusher).Flush()
		panic(http.ErrAbortHandler) // connection drops mid-body
	}
	f.installAndWait(false)
	if f.phase() != "error" {
		t.Fatalf("want error after drop, got %v", f.w.prog.snapshot())
	}
	partial := f.modelPath() + ".partial" // same name the Python uses
	st, err := os.Stat(partial)
	if err != nil || st.Size() == 0 || st.Size() >= int64(len(f.model)) {
		t.Fatalf("partial missing or wrong size: %v %v", st, err)
	}
	have := st.Size()
	f.installAndWait(false)
	if f.phase() != "ready" {
		t.Fatalf("resume failed: %v", f.w.prog.snapshot())
	}
	reqs := f.requests(nvTestModelFile)
	if last := reqs[len(reqs)-1]; last.rng != fmt.Sprintf("bytes=%d-", have) {
		t.Fatalf("no Range resume, last request %+v", last)
	}
	got, _ := os.ReadFile(f.modelPath())
	if !bytes.Equal(got, f.model) {
		t.Fatal("resumed file is corrupt")
	}
	if _, err := os.Stat(partial); err == nil {
		t.Fatal("partial left behind")
	}
	if f.w.prog.snapshot()["resumed"] != true {
		t.Fatalf("resumed flag: %v", f.w.prog.snapshot())
	}
}

func TestVisionNativeChecksumMismatchInstallsNothing(t *testing.T) {
	t.Run("model asset", func(t *testing.T) {
		f := newNVFixture(t, nil)
		f.w.models[0].MMProjSHA256 = nvTestSum([]byte("something else"))
		f.installAndWait(false)
		snap := f.w.prog.snapshot()
		if snap["phase"] != "error" || !strings.Contains(snap["error"].(string), "SHA256 mismatch") {
			t.Fatalf("progress: %v", snap)
		}
		if fileExists(filepath.Join(visionModelsDir(f.home, visionDefaultModelID), nvTestMMProjFile)) ||
			fileExists(filepath.Join(visionModelsDir(f.home, visionDefaultModelID), nvTestMMProjFile+".partial")) {
			t.Fatal("bad file kept")
		}
		if fileExists(visionJSONPath(f.home)) || visionInstalled(f.home, visionDefaultModelID) {
			t.Fatal("reported installed")
		}
	})
	t.Run("runtime archive is never extracted", func(t *testing.T) {
		f := newNVFixture(t, nil)
		f.w.runtimes[f.rtID] = nvRuntime{ID: f.rtID, Platform: f.rtID, Tag: "tfake", ZipName: f.rtName,
			Size: int64(len(f.archive)), SHA256: nvTestSum([]byte("not the archive"))}
		f.installAndWait(false)
		if f.phase() != "error" {
			t.Fatalf("phase %s", f.phase())
		}
		if ents, _ := os.ReadDir(visionRuntimeDir(f.home)); len(ents) != 0 {
			t.Fatalf("extracted unverified archive: %v", ents)
		}
		if len(f.requests(nvTestModelFile)) != 0 {
			t.Fatal("went on to download the model")
		}
	})
}

func TestVisionNativeCancelKeepsPartial(t *testing.T) {
	f := newNVFixture(t, nil)
	release := make(chan struct{})
	t.Cleanup(func() { close(release) })
	f.hook = func(w http.ResponseWriter, r *http.Request, name string, body []byte) bool {
		if name != nvTestModelFile {
			return false
		}
		w.Header().Set("Content-Length", strconv.Itoa(len(body)))
		w.WriteHeader(200)
		w.Write(body[:len(body)/3])
		w.(http.Flusher).Flush()
		select {
		case <-release:
		case <-r.Context().Done():
		}
		return true
	}
	if _, err := f.w.Install(context.Background(), f.home, "", "", false); err != nil {
		t.Fatal(err)
	}
	partial := f.modelPath() + ".partial"
	deadline := time.Now().Add(10 * time.Second)
	for {
		if st, err := os.Stat(partial); err == nil && st.Size() > 0 {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("download never started: %v", f.w.prog.snapshot())
		}
		time.Sleep(5 * time.Millisecond)
	}
	start := time.Now()
	res, _ := f.w.CancelInstall(context.Background(), f.home)
	if res["ok"] != true {
		t.Fatalf("cancel: %v", res)
	}
	f.wait()
	if time.Since(start) > 3*time.Second {
		t.Fatalf("cancel took %v", time.Since(start))
	}
	if f.phase() != "cancelled" || f.w.prog.snapshot()["error"] != nil {
		t.Fatalf("progress: %v", f.w.prog.snapshot())
	}
	if _, err := os.Stat(partial); err != nil {
		t.Fatal("partial file not kept for resume")
	}
	if fileExists(visionJSONPath(f.home)) {
		t.Fatal("vision.json written")
	}
	if res, _ := f.w.CancelInstall(context.Background(), f.home); res["ok"] != false || res["error"] != "No install in progress" {
		t.Fatalf("second cancel: %v", res)
	}
}

func nvTestZip(t *testing.T, entries map[string]string, symlinks map[string]string) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "a.zip")
	var buf bytes.Buffer
	zw := zip.NewWriter(&buf)
	for n, c := range entries {
		w, _ := zw.Create(n)
		w.Write([]byte(c))
	}
	for n, target := range symlinks {
		h := &zip.FileHeader{Name: n}
		h.SetMode(os.ModeSymlink | 0o777)
		w, _ := zw.CreateHeader(h)
		w.Write([]byte(target))
	}
	zw.Close()
	os.WriteFile(p, buf.Bytes(), 0o644)
	return p
}

func nvTestTar(t *testing.T, hdrs []tar.Header) string {
	t.Helper()
	p := filepath.Join(t.TempDir(), "a.tar.gz")
	var buf bytes.Buffer
	gz := gzip.NewWriter(&buf)
	tw := tar.NewWriter(gz)
	for _, h := range hdrs {
		h := h
		if h.Typeflag == 0 {
			h.Typeflag = tar.TypeReg
		}
		h.Size = 0
		if h.Typeflag == tar.TypeReg {
			h.Size = 4
		}
		tw.WriteHeader(&h)
		if h.Size > 0 {
			tw.Write([]byte("data"))
		}
	}
	tw.Close()
	gz.Close()
	os.WriteFile(p, buf.Bytes(), 0o644)
	return p
}

func TestVisionNativeExtractRefusesTraversal(t *testing.T) {
	ctx := context.Background()
	for name, entries := range map[string]map[string]string{
		"parent":       {"../evil.txt": "x"},
		"nested":       {"a/../../evil.txt": "x"},
		"backslash":    {`..\evil.txt`: "x"},
		"absolute":     {"/abs-evil.txt": "x"},
		"drive letter": {"C:/evil.txt": "x"},
	} {
		t.Run("zip "+name, func(t *testing.T) {
			parent := t.TempDir()
			dest := filepath.Join(parent, "rt")
			if err := nvExtractArchive(ctx, nvTestZip(t, entries, nil), dest); err == nil || !strings.Contains(err.Error(), "blocked") {
				t.Fatalf("want blocked, got %v", err)
			}
			if fileExists(filepath.Join(parent, "evil.txt")) || fileExists(`C:\evil.txt`) {
				t.Fatal("file escaped")
			}
		})
	}
	t.Run("zip symlink out", func(t *testing.T) {
		err := nvExtractArchive(ctx, nvTestZip(t, nil, map[string]string{"lnk": "../../outside"}), filepath.Join(t.TempDir(), "rt"))
		if err == nil || !strings.Contains(err.Error(), "blocked") {
			t.Fatalf("got %v", err)
		}
	})
	t.Run("zip fine", func(t *testing.T) {
		dest := filepath.Join(t.TempDir(), "rt")
		if err := nvExtractArchive(ctx, nvTestZip(t, map[string]string{"bin/llama-server.exe": "x", "d/": ""}, nil), dest); err != nil {
			t.Fatal(err)
		}
		if !fileExists(filepath.Join(dest, "bin", "llama-server.exe")) {
			t.Fatal("not extracted")
		}
	})
	for name, h := range map[string]tar.Header{
		"parent":           {Name: "../evil"},
		"absolute":         {Name: "/evil"},
		"abs symlink":      {Name: "l", Typeflag: tar.TypeSymlink, Linkname: "/etc/passwd"},
		"escaping symlink": {Name: "d/l", Typeflag: tar.TypeSymlink, Linkname: "../../outside"},
		"hardlink out":     {Name: "h", Typeflag: tar.TypeLink, Linkname: "../x"},
	} {
		h := h
		t.Run("tar "+name, func(t *testing.T) {
			parent := t.TempDir()
			dest := filepath.Join(parent, "rt")
			if err := nvExtractArchive(ctx, nvTestTar(t, []tar.Header{h}), dest); err == nil || !strings.Contains(err.Error(), "blocked") {
				t.Fatalf("want blocked, got %v", err)
			}
			if fileExists(filepath.Join(parent, "evil")) {
				t.Fatal("file escaped")
			}
		})
	}
	t.Run("tar relative in-archive symlink is allowed", func(t *testing.T) {
		dest := filepath.Join(t.TempDir(), "rt")
		err := nvExtractArchive(ctx, nvTestTar(t, []tar.Header{
			{Name: "libllama.so.0.0.1", Mode: 0o755},
			{Name: "libllama.so.0", Typeflag: tar.TypeSymlink, Linkname: "libllama.so.0.0.1"},
		}), dest)
		if err != nil && runtime.GOOS == "windows" && strings.Contains(err.Error(), "privilege") {
			t.Skip("symlinks need privilege on this Windows account")
		}
		if err != nil {
			t.Fatal(err)
		}
	})
}

func TestVisionNativeInstallRefusesTraversalArchive(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("zip path; the tar variant is covered by TestVisionNativeExtractRefusesTraversal")
	}
	f := newNVFixture(t, map[string][]byte{"..\\escape.txt": []byte("x"), nvTestBinName(): []byte("x")})
	f.installAndWait(false)
	if f.phase() != "error" || !strings.Contains(f.w.prog.snapshot()["error"].(string), "Slip blocked") {
		t.Fatalf("progress: %v", f.w.prog.snapshot())
	}
	if fileExists(filepath.Join(visionHome(f.home), "escape.txt")) || fileExists(filepath.Join(filepath.Dir(visionRuntimeDir(f.home)), "escape.txt")) {
		t.Fatal("escaped")
	}
	if len(f.requests(nvTestModelFile)) != 0 {
		t.Fatal("kept going after a refused archive")
	}
}

func nvSeedInstall(t *testing.T, f *nvFixture) {
	t.Helper()
	mdir := visionModelsDir(f.home, visionDefaultModelID)
	os.MkdirAll(mdir, 0o755)
	os.WriteFile(filepath.Join(mdir, nvTestModelFile), f.model, 0o644)
	os.WriteFile(filepath.Join(mdir, nvTestMMProjFile), f.mmproj, 0o644)
	os.MkdirAll(visionRuntimeDir(f.home), 0o755)
	os.WriteFile(filepath.Join(visionRuntimeDir(f.home), nvTestBinName()), []byte("bin"), 0o755)
	os.MkdirAll(nvDownloadsDir(f.home), 0o755)
	os.WriteFile(filepath.Join(nvDownloadsDir(f.home), "x.zip"), []byte("z"), 0o644)
	writeJSONAtomic(visionJSONPath(f.home), map[string]any{"model_id": visionDefaultModelID})
}

func TestVisionNativeUninstall(t *testing.T) {
	ctx := context.Background()
	t.Run("keep models", func(t *testing.T) {
		f := newNVFixture(t, nil)
		nvSeedInstall(t, f)
		res, _ := f.w.Uninstall(ctx, f.home, true)
		if res["ok"] != true || res["removed"] != true || res["keep_models"] != true {
			t.Fatalf("res %v", res)
		}
		if !fileExists(f.modelPath()) {
			t.Fatal("model deleted")
		}
		for _, p := range []string{visionRuntimeDir(f.home), nvDownloadsDir(f.home), visionJSONPath(f.home)} {
			if _, err := os.Stat(p); err == nil {
				t.Fatalf("%s survived", p)
			}
		}
		if f.phase() != "idle" {
			t.Fatalf("phase %s", f.phase())
		}
	})
	t.Run("everything", func(t *testing.T) {
		f := newNVFixture(t, nil)
		nvSeedInstall(t, f)
		res, _ := f.w.Uninstall(ctx, f.home, false)
		if res["removed"] != true || res["keep_models"] != false {
			t.Fatalf("res %v", res)
		}
		if _, err := os.Stat(visionHome(f.home)); err == nil {
			t.Fatal("vision dir survived")
		}
	})
	t.Run("nothing there", func(t *testing.T) {
		f := newNVFixture(t, nil)
		res, _ := f.w.Uninstall(ctx, f.home, false)
		if res["ok"] != true || res["removed"] != false || len(res) != 2 {
			t.Fatalf("res %v", res)
		}
	})
}

func TestVisionNativeReinstallRuntime(t *testing.T) {
	f := newNVFixture(t, nil)
	f.installAndWait(false)
	if f.phase() != "ready" {
		t.Fatal(f.w.prog.snapshot())
	}
	marker := filepath.Join(visionRuntimeDir(f.home), "stale-marker")
	os.WriteFile(marker, []byte("old"), 0o644)
	modelReqs := len(f.requests(nvTestModelFile))
	rtReqs := len(f.requests(f.rtName))

	res, err := f.w.ReinstallRuntime(context.Background(), f.home, false)
	if err != nil || res["ok"] != true {
		t.Fatalf("res %v err %v", res, err)
	}
	if _, ok := res["health"].(map[string]any); !ok || res["warnings"] == nil {
		t.Fatalf("missing health/warnings: %v", res)
	}
	f.wait()
	if f.phase() != "ready" {
		t.Fatal(f.w.prog.snapshot())
	}
	if fileExists(marker) {
		t.Fatal("old runtime tree was kept")
	}
	if !fileExists(filepath.Join(visionRuntimeDir(f.home), nvTestBinName())) {
		t.Fatal("runtime not re-extracted")
	}
	if len(f.requests(f.rtName)) != rtReqs+1 {
		t.Fatal("runtime archive not fetched fresh")
	}
	if len(f.requests(nvTestModelFile)) != modelReqs {
		t.Fatal("model was downloaded again")
	}
	// A running install blocks a reinstall.
	f.w.prog.update(map[string]any{"phase": "downloading"})
	if res, _ := f.w.ReinstallRuntime(context.Background(), f.home, false); res["ok"] != false || res["error"] != "Install already in progress" {
		t.Fatalf("res %v", res)
	}
}

func TestVisionNativeStartNotInstalled(t *testing.T) {
	f := newNVFixture(t, nil)
	res, err := f.w.Start(context.Background(), f.home)
	if err != nil {
		t.Fatal(err)
	}
	want := "Pinned local model files not found. Reinstall Remedy Desktop (bundles qwen35-4b-mind) or set REMEDY_LOCAL_BUNDLE."
	if res["ok"] != false || res["error"] != want || len(res) != 2 {
		t.Fatalf("res %v", res)
	}
	// State present but its model file is gone and nothing can be activated.
	writeJSONAtomic(visionJSONPath(f.home), map[string]any{"model_id": visionDefaultModelID, "model_path": filepath.Join(f.home, "gone.gguf")})
	res, _ = f.w.Start(context.Background(), f.home)
	if res["ok"] != false || res["error"] != "Model file missing: "+filepath.Join(f.home, "gone.gguf") {
		t.Fatalf("res %v", res)
	}
	// RMB park switch.
	writeJSONAtomic(rmbJSONPath(f.home), map[string]any{"vision_suspended": true})
	res, _ = f.w.Start(context.Background(), f.home)
	if res["skipped"] != true || res["reason"] != "rmb_local_agent" {
		t.Fatalf("res %v", res)
	}
}

func TestVisionNativeActivateBundle(t *testing.T) {
	ctx := context.Background()
	t.Run("files under home", func(t *testing.T) {
		f := newNVFixture(t, nil)
		nvSeedInstall(t, f)
		os.Remove(visionJSONPath(f.home))
		res, err := f.w.Activate(ctx, f.home, true)
		if err != nil {
			t.Fatal(err)
		}
		if res["ok"] != true || res["mode"] != "local_files" || res["message"] == nil {
			t.Fatalf("res %v", res)
		}
		if _, ok := res["server"].(map[string]any); !ok || res["running"] != false {
			t.Fatalf("server not attempted: %v", res)
		}
		if res["progress"] == nil || res["model_id"] == nil { // status payload merged in
			t.Fatalf("status keys missing: %v", res)
		}
		st := loadVisionJSON(f.home)
		if st["model_path"] != f.modelPath() || st["bundled"] != true || st["enabled"] != true ||
			st["backend"] != "llama_server" || st["runtime_version"] != "bundled" ||
			st["runtime_binary"] != filepath.Join(visionRuntimeDir(f.home), nvTestBinName()) {
			t.Fatalf("vision.json: %v", st)
		}
	})
	t.Run("product bundle", func(t *testing.T) {
		f := newNVFixture(t, nil)
		root := filepath.Join(t.TempDir(), "local")
		t.Setenv("REMEDY_LOCAL_BUNDLE", root)
		md := filepath.Join(root, "models", visionDefaultModelID)
		os.MkdirAll(md, 0o755)
		os.WriteFile(filepath.Join(md, nvTestModelFile), f.model, 0o644)
		os.WriteFile(filepath.Join(md, nvTestMMProjFile), f.mmproj, 0o644)
		os.MkdirAll(filepath.Join(root, "runtime", "cpu"), 0o755)
		bin := filepath.Join(root, "runtime", "cpu", nvTestBinName())
		os.WriteFile(bin, []byte("x"), 0o755)
		if !f.w.isInstalled(f.home, f.w.models[0]) {
			t.Fatal("bundle not recognised as installed")
		}
		res, _ := f.w.Activate(ctx, f.home, false)
		st := loadVisionJSON(f.home)
		if res["ok"] != true || st["runtime_binary"] != bin || st["model_path"] != filepath.Join(md, nvTestModelFile) || st["enabled"] != false {
			t.Fatalf("res %v state %v", res, st)
		}
		if _, ran := res["server"]; ran {
			t.Fatal("must not start when disabled")
		}
	})
	t.Run("nothing to activate", func(t *testing.T) {
		f := newNVFixture(t, nil)
		res, _ := f.w.Activate(ctx, f.home, true)
		if res["ok"] != false || res["mode"] != "missing" || res["error"] == nil {
			t.Fatalf("res %v", res)
		}
		d, ok := res["diagnostic"].(map[string]any)
		if !ok || d["model_present"] != false || d["cpu_binary"] != nil {
			t.Fatalf("diagnostic %v", res["diagnostic"])
		}
	})
}

func TestVisionNativeInstallAlreadyOnDisk(t *testing.T) {
	f := newNVFixture(t, nil)
	nvSeedInstall(t, f)
	res, _ := f.w.Install(context.Background(), f.home, "", "", false)
	if res["mode"] != "already_installed" || res["ok"] != true {
		t.Fatalf("res %v", res)
	}
	if len(f.requests("")) != 0 {
		t.Fatal("downloaded although installed")
	}
}

func TestVisionNativeCommandLine(t *testing.T) {
	cmd := nvBuildCommand("/b/llama-server", "/m/Qwen3.5-4B-Q6_K.gguf", "/m/mmproj.gguf", "127.0.0.1", 8740, -1, ConfigMap{}, nil)
	want := "/b/llama-server -m /m/Qwen3.5-4B-Q6_K.gguf --mmproj /m/mmproj.gguf --host 127.0.0.1 --port 8740 --ctx-size 8192 -ngl -1 " +
		"--parallel 2 --kv-unified-per-slot 8192 --flash-attn on --jinja --chat-template-kwargs {\"enable_thinking\": false} " +
		"--cache-prompt --cache-ram 4096 --cache-idle-slots --mmap --cache-type-k q8_0 --cache-type-v q8_0 " +
		"--image-max-tokens 1024 --spec-type draft-mtp --spec-draft-n-max 3"
	if got := strings.Join(cmd, " "); got != want {
		t.Fatalf("got  %s\nwant %s", got, want)
	}
	fb := nvCommandFallbacks(cmd)
	if len(fb) != 4 || !strings.HasSuffix(strings.Join(fb[0], " "), "--spec-type ngram-mod") ||
		strings.Contains(strings.Join(fb[1], " "), "--spec-type") ||
		strings.Contains(strings.Join(fb[2], " "), "--flash-attn") ||
		strings.Contains(strings.Join(fb[3], " "), "--cache-type-k") {
		t.Fatalf("fallbacks: %v", fb)
	}
	// An older build that only knows -fa and no MTP.
	old := nvBuildCommand("b", "m.gguf", "p", "h", 1, 0, ConfigMap{}, []byte("--parallel -fa --ngram-mod --spec-type --jinja"))
	j := strings.Join(old, " ")
	if !strings.Contains(j, " -fa") || strings.Contains(j, "draft-mtp") || !strings.Contains(j, "--spec-type ngram-mod") {
		t.Fatalf("old build: %s", j)
	}
}

// ---- real process: needs `go` to build a tiny fake llama-server ----------------

const nvFakeServerSrc = `package main

import (
	"fmt"
	"net/http"
	"os"
)

func main() {
	host, port := "127.0.0.1", "0"
	for i, a := range os.Args {
		if a == "--host" && i+1 < len(os.Args) {
			host = os.Args[i+1]
		}
		if a == "--port" && i+1 < len(os.Args) {
			port = os.Args[i+1]
		}
	}
	if f := os.Getenv("FAKE_ARGS_FILE"); f != "" {
		os.WriteFile(f, []byte(fmt.Sprint(os.Args[1:])), 0o644)
	}
	http.HandleFunc("/", func(w http.ResponseWriter, r *http.Request) { w.Write([]byte("{}")) })
	if err := http.ListenAndServe(host+":"+port, nil); err != nil {
		fmt.Fprintln(os.Stderr, err)
		os.Exit(2)
	}
}
`

func nvBuildFakeServer(t *testing.T) string {
	t.Helper()
	goBin, err := exec.LookPath("go")
	if err != nil {
		t.Skip("go toolchain not on PATH; cannot build the fake llama-server")
	}
	dir := t.TempDir()
	src := filepath.Join(dir, "fake.go")
	os.WriteFile(src, []byte(nvFakeServerSrc), 0o644)
	out := filepath.Join(dir, nvTestBinName())
	cmd := exec.Command(goBin, "build", "-o", out, src)
	cmd.Dir = dir
	if b, err := cmd.CombinedOutput(); err != nil {
		t.Skipf("cannot build fake server: %v\n%s", err, b)
	}
	return out
}

// nvExecSpawn stands in for the Zig host: os/exec is only allowed in tests.
func nvExecSpawn(_ context.Context, _ string, argv []string, cwd string) (*visionProc, error) {
	cmd := exec.Command(argv[0], argv[1:]...)
	cmd.Dir = cwd
	if err := cmd.Start(); err != nil {
		return nil, err
	}
	done := make(chan struct{})
	go func() { cmd.Wait(); close(done) }()
	return &visionProc{
		PID: uint32(cmd.Process.Pid),
		Exited: func() (int, bool) {
			select {
			case <-done:
				return cmd.ProcessState.ExitCode(), true
			default:
				return 0, false
			}
		},
		Kill: func() error { err := cmd.Process.Kill(); <-done; return err },
	}, nil
}

func nvFreePort(t *testing.T) int {
	l, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	defer l.Close()
	return l.Addr().(*net.TCPAddr).Port
}

func TestVisionNativeStartStop(t *testing.T) {
	fake := nvBuildFakeServer(t)
	f := newNVFixture(t, nil)
	nvSeedInstall(t, f)
	data, _ := os.ReadFile(fake)
	bin := filepath.Join(visionRuntimeDir(f.home), nvTestBinName())
	os.WriteFile(bin, data, 0o755)
	port := nvFreePort(t)
	argsFile := filepath.Join(t.TempDir(), "args.txt")
	t.Setenv("FAKE_ARGS_FILE", argsFile)
	writeJSONAtomic(visionJSONPath(f.home), map[string]any{
		"model_id": visionDefaultModelID, "model_path": f.modelPath(),
		"mmproj_path":    filepath.Join(filepath.Dir(f.modelPath()), nvTestMMProjFile),
		"runtime_binary": bin, "host": "127.0.0.1", "port": port,
		"base_url": fmt.Sprintf("http://127.0.0.1:%d/v1", port),
	})
	nativeVisionSpawn = nvExecSpawn
	t.Cleanup(func() { f.w.Stop(context.Background(), f.home) })

	ctx := context.Background()
	res, err := f.w.Start(ctx, f.home)
	if err != nil || res["ok"] != true || res["already_running"] != false {
		t.Fatalf("start: %v %v", res, err)
	}
	pid := res["pid"].(int)
	if pid <= 0 || res["base_url"] != fmt.Sprintf("http://127.0.0.1:%d/v1", port) {
		t.Fatalf("start result %v", res)
	}
	if !nvHealth(res["base_url"].(string), time.Second) {
		t.Fatal("server not healthy")
	}
	args, _ := os.ReadFile(argsFile)
	for _, want := range []string{"-m " + f.modelPath(), "--mmproj", "--host 127.0.0.1", "--port " + strconv.Itoa(port), "-ngl -1", "--ctx-size 8192"} {
		if !strings.Contains(strings.Trim(string(args), "[]"), want) {
			t.Fatalf("args %q lack %q", args, want)
		}
	}
	if st := loadVisionJSON(f.home); anyInt(st["pid"], 0) != pid {
		t.Fatalf("pid not recorded: %v", st)
	}

	again, _ := f.w.Start(ctx, f.home)
	if again["ok"] != true || again["already_running"] != true || again["pid"] != pid {
		t.Fatalf("second start: %v", again)
	}

	stop, _ := f.w.Stop(ctx, f.home)
	if stop["ok"] != true || stop["stopped"] != true {
		t.Fatalf("stop: %v", stop)
	}
	deadline := time.Now().Add(5 * time.Second)
	for visionPortOpen("127.0.0.1", port) {
		if time.Now().After(deadline) {
			t.Fatal("server still listening after stop")
		}
		time.Sleep(20 * time.Millisecond)
	}
	if st := loadVisionJSON(f.home); st["pid"] != nil {
		t.Fatalf("pid not cleared: %v", st)
	}
	if again, _ := f.w.Stop(ctx, f.home); again["ok"] != true || again["stopped"] != false {
		t.Fatalf("second stop: %v", again)
	}
}

func TestVisionNativeStartFallsBackWhenServerExitsEarly(t *testing.T) {
	f := newNVFixture(t, nil)
	nvSeedInstall(t, f)
	port := nvFreePort(t)
	writeJSONAtomic(visionJSONPath(f.home), map[string]any{
		"model_id": visionDefaultModelID, "model_path": f.modelPath(),
		"mmproj_path":    filepath.Join(filepath.Dir(f.modelPath()), nvTestMMProjFile),
		"runtime_binary": filepath.Join(visionRuntimeDir(f.home), nvTestBinName()),
		"host":           "127.0.0.1", "port": port, "base_url": fmt.Sprintf("http://127.0.0.1:%d/v1", port),
	})
	var spawned [][]string
	nativeVisionSpawn = func(_ context.Context, _ string, argv []string, _ string) (*visionProc, error) {
		spawned = append(spawned, argv)
		return &visionProc{PID: 1, Exited: func() (int, bool) { return 3, true }, Kill: func() error { return nil }}, nil
	}
	res, _ := f.w.Start(context.Background(), f.home)
	if res["ok"] != false || res["error"] != "llama-server exited early (code 3)" {
		t.Fatalf("res %v", res)
	}
	if len(spawned) != 5 { // original + 4 softer variants
		t.Fatalf("tried %d command lines", len(spawned))
	}
}
