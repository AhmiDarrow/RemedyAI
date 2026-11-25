package httpapi

// llama-server process management and bundle activation for the native
// VisionWorker. Spec: vision/runtime.py (start_server, stop_server, _health),
// runtime/bundle.py (activate_local_bundle), runtime/mind_engine.py (command).

import (
	"context"
	"encoding/json"
	"fmt"
	"io/fs"
	"net"
	"net/http"
	"net/url"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// visionProc is one supervised llama-server child. Kill terminates the whole
// tree and releases the handle (closing the job kills any leftover child).
type visionProc struct {
	PID    uint32
	Exited func() (code int, exited bool)
	Kill   func() error
}

// Overridable by tests; production goes through the Zig host like every other
// child process in this repo (os/exec is forbidden here).
var (
	nativeVisionSpawn  = zigVisionSpawn
	nativeVisionHelp   = zigVisionHelp
	nativeVisionKill   = func(pid uint32) error { return core.ProcessKillTree(pid) }
	nativeVisionPoll   = 400 * time.Millisecond
	nativeVisionStartW = 60 * time.Second
)

// zigVisionSpawn starts argv hidden (no console window on Windows) inside a
// job object / process group owned by the runtime process: when the Go runtime
// exits, closing the handle takes llama-server down with it.
func zigVisionSpawn(ctx context.Context, home string, argv []string, cwd string) (*visionProc, error) {
	p, err := zigTunnelStarter(home)(ctx, argv, nil, cwd)
	if err != nil {
		return nil, err
	}
	return &visionProc{
		PID: p.PID,
		Exited: func() (int, bool) {
			code, err := core.ProcessWait(p.Handle, 0)
			if err != nil || code == nil {
				return 0, false
			}
			return int(*code), true
		},
		Kill: p.Cleanup,
	}, nil
}

func zigVisionHelp(home, binary string) []byte {
	argv := []string{binary, "--help"}
	key, err := secret.EnsureHostSigningKey(home)
	if err != nil || core.EnsureSigningKey(key) != nil {
		return nil
	}
	token, nowMS, err := core.IssueProcessSpawnToken(argv, nil, false, false)
	if err != nil {
		return nil
	}
	var res core.ExecCaptureResult
	if err := core.WithWriteJail(nil, func() error {
		var err error
		res, err = core.ExecCaptureAuthorized(argv, "", nil, false, token, "", "", false, nowMS, 8000)
		return err
	}); err != nil {
		return nil
	}
	return append(res.Stdout, res.Stderr...)
}

// ---- bundle (runtime/bundle.py) ----------------------------------------------

func nvBundleRoots() []string {
	var roots []string
	for _, k := range []string{"REMEDY_LOCAL_BUNDLE", "REMEDY_BUNDLE_DIR"} {
		if v := strings.TrimSpace(os.Getenv(k)); v != "" {
			roots = append(roots, v)
			break
		}
	}
	for _, k := range []string{"REMEDY_RESOURCES", "TAURI_RESOURCE_DIR", "RESOURCE_DIR"} {
		if v := strings.TrimSpace(os.Getenv(k)); v != "" {
			if filepath.Base(v) != "local" {
				v = filepath.Join(v, "local")
			}
			roots = append(roots, v)
		}
	}
	if exe, err := os.Executable(); err == nil {
		base := filepath.Dir(exe)
		roots = append(roots, filepath.Join(base, "local"), filepath.Join(base, "resources", "local"),
			filepath.Join(filepath.Dir(base), "resources", "local"))
	}
	if cwd, err := os.Getwd(); err == nil {
		roots = append(roots, filepath.Join(cwd, "resources", "local"), filepath.Join(cwd, "local"))
	}
	seen := map[string]bool{}
	var out []string
	for _, r := range roots {
		key := r
		if abs, err := filepath.Abs(r); err == nil {
			key = abs
		}
		if !seen[key] {
			seen[key] = true
			out = append(out, r)
		}
	}
	return out
}

type nvModelPaths struct{ model, mmproj, root string }

func (w *nativeVisionWorker) modelPathsFromBundle(m visionModelSpec, root string) *nvModelPaths {
	roots := []string{root}
	if root == "" {
		roots = nvBundleRoots()
	}
	for _, r := range roots {
		for _, d := range []string{filepath.Join(r, "models", m.ID), filepath.Join(r, m.ID), filepath.Join(r, "models")} {
			if fileExists(filepath.Join(d, m.ModelFile)) && fileExists(filepath.Join(d, m.MMProjFile)) {
				return &nvModelPaths{filepath.Join(d, m.ModelFile), filepath.Join(d, m.MMProjFile), r}
			}
		}
	}
	return nil
}

func nvFindLlama(roots []string) string {
	for _, r := range roots {
		if st, err := os.Stat(r); err != nil || r == "" {
			continue
		} else if !st.IsDir() {
			continue
		}
		for _, n := range []string{"llama-server.exe", "llama-server"} {
			if p := filepath.Join(r, n); fileExists(p) {
				return p
			}
		}
		for _, n := range []string{"llama-server.exe", "llama-server"} {
			found := ""
			_ = filepath.WalkDir(r, func(p string, d fs.DirEntry, err error) error {
				if err == nil && !d.IsDir() && d.Name() == n {
					found = p
					return filepath.SkipAll
				}
				return nil
			})
			if found != "" {
				return found
			}
		}
	}
	return ""
}

func (w *nativeVisionWorker) runtimeBinaryFromBundle(runtimeID, bundleRoot string) string {
	rid := w.normalizeRuntime(runtimeID, false)
	flavor := "cpu"
	if strings.Contains(rid, "cuda") {
		flavor = "cuda"
	} else if strings.Contains(rid, "vulkan") {
		flavor = "vulkan"
	}
	root := bundleRoot
	if root == "" {
		if p := w.modelPathsFromBundle(visionModels[0], ""); p != nil {
			root = p.root
		}
	}
	var roots []string
	if root != "" {
		if st, err := os.Stat(filepath.Join(root, "runtime")); err == nil && st.IsDir() {
			roots = append(roots, filepath.Join(root, "runtime"))
		}
		roots = append(roots, filepath.Join(root, "runtime", flavor), filepath.Join(root, "runtime", rid),
			filepath.Join(root, "runtime"), root)
	}
	for _, r := range nvBundleRoots() {
		roots = append(roots, filepath.Join(r, "runtime", flavor), filepath.Join(r, "runtime"), r)
	}
	return nvFindLlama(roots)
}

func (w *nativeVisionWorker) bundleAvailable() map[string]any {
	m, _ := w.model(visionDefaultModelID)
	p := w.modelPathsFromBundle(m, "")
	str := func(s string) any {
		if s == "" {
			return nil // Python str(None) leaks the text "None" here; null is the honest value
		}
		return s
	}
	cpu := w.runtimeBinaryFromBundle(visionDefaultRuntime(false), "")
	gpu := w.runtimeBinaryFromBundle(visionDefaultRuntime(true), "")
	roots := nvBundleRoots()
	if len(roots) > 12 {
		roots = roots[:12]
	}
	out := map[string]any{
		"model_present": p != nil, "model_path": nil, "mmproj_path": nil, "bundle_root": nil,
		"cpu_binary": str(cpu), "cuda_binary": str(gpu), "gpu_binary": str(gpu),
		"searched_roots": roots,
	}
	if p != nil {
		out["model_path"], out["mmproj_path"], out["bundle_root"] = p.model, p.mmproj, p.root
	}
	return out
}

func (w *nativeVisionWorker) activateLocalBundle(home string, enabled bool) map[string]any {
	m, _ := w.model(visionDefaultModelID)
	paths := w.modelPathsFromBundle(m, "")
	if paths == nil { // legacy: files already under this home's vision/models
		d := visionModelsDir(home, m.ID)
		if fileExists(filepath.Join(d, m.ModelFile)) && fileExists(filepath.Join(d, m.MMProjFile)) {
			paths = &nvModelPaths{filepath.Join(d, m.ModelFile), filepath.Join(d, m.MMProjFile), visionHome(home)}
		}
	}
	if paths == nil {
		return map[string]any{
			"ok": false,
			"error": "Pinned local model files not found. Reinstall Remedy Desktop " +
				"(bundles " + visionDefaultModelID + ") or set REMEDY_LOCAL_BUNDLE.",
			"diagnostic": w.bundleAvailable(),
		}
	}
	rid := visionDefaultRuntime(nvNvidiaPresent())
	bin := w.runtimeBinaryFromBundle(rid, paths.root)
	if bin == "" {
		rid = visionDefaultRuntime(false)
		bin = w.runtimeBinaryFromBundle(rid, paths.root)
	}
	if bin == "" {
		bin = nativeVisionServerBinary(home)
	}
	if bin == "" {
		bin = nvFindLlama([]string{visionRuntimeDir(home), filepath.Join(paths.root, "runtime")})
	}
	if bin == "" {
		return map[string]any{
			"ok":         false,
			"error":      "llama-server binary not found next to model (CPU/CUDA bundle).",
			"diagnostic": w.bundleAvailable(),
		}
	}
	state := loadVisionJSON(home)
	if state["host"] == nil || anyString(state["host"]) == "" {
		state["host"] = visionDefaultHost
	}
	if anyInt(state["port"], 0) == 0 {
		state["port"] = visionDefaultPort
	}
	if anyString(state["base_url"]) == "" {
		state["base_url"] = fmt.Sprintf("http://%s:%d/v1", visionDefaultHost, visionDefaultPort)
	}
	if anyString(state["runtime_version"]) == "" {
		state["runtime_version"] = "bundled"
	}
	state["model_id"] = visionDefaultModelID
	state["model_path"], state["mmproj_path"] = paths.model, paths.mmproj
	state["runtime_id"], state["backend"], state["bundled"] = rid, "llama_server", true
	state["runtime_binary"] = bin
	if _, has := state["enabled"]; !has || state["enabled"] == nil {
		state["enabled"] = enabled
	}
	if enabled {
		state["enabled"] = true
	}
	if err := writeJSONAtomic(visionJSONPath(home), state); err != nil {
		return map[string]any{"ok": false, "error": err.Error()}
	}
	return map[string]any{"ok": true, "state": state, "runtime_id": rid, "bundled": true}
}

// isInstalled mirrors install.is_installed: files under this home, or a
// product bundle (resources/local, REMEDY_LOCAL_BUNDLE) plus a runtime.
func (w *nativeVisionWorker) isInstalled(home string, m visionModelSpec) bool {
	d := visionModelsDir(home, m.ID)
	if fileExists(filepath.Join(d, m.ModelFile)) && fileExists(filepath.Join(d, m.MMProjFile)) &&
		nativeVisionServerBinary(home) != "" {
		return true
	}
	p := w.modelPathsFromBundle(m, "")
	if p == nil {
		return false
	}
	slash := strings.ToLower(filepath.ToSlash(p.root))
	product := filepath.Base(p.root) == "local" || strings.Contains(slash, "/resources/local") ||
		strings.Contains(slash, "/bundled/local")
	if env := strings.TrimSpace(os.Getenv("REMEDY_LOCAL_BUNDLE")); env != "" {
		a, _ := filepath.Abs(env)
		b, _ := filepath.Abs(p.root)
		product = product || a == b || nvWithin(a, b)
	}
	if !product {
		return false
	}
	return w.runtimeBinaryFromBundle("", "") != "" || nativeVisionServerBinary(home) != ""
}

// activateBundle is service.activate_bundle.
func (w *nativeVisionWorker) activateBundle(home string, enabled, start bool) map[string]any {
	res := w.activateLocalBundle(home, enabled)
	// The status payload the HTTP layer serves, so both paths agree on keys.
	out := (&Server{homeDir: home, vision: w}).visionStatusPayload(false)
	ok := anyBoolDef(res["ok"], false)
	out["ok"] = ok
	if e := anyString(res["error"]); e != "" {
		out["error"] = e
	}
	if s := res["state"]; s != nil {
		out["state"] = s
	}
	if !ok {
		out["mode"] = "missing"
		out["diagnostic"] = res["diagnostic"]
		return out
	}
	out["mode"] = "local_files"
	out["message"] = "Local model files activated."
	if start && enabled {
		started := w.ensureServer(context.Background(), home)
		out["server"] = started
		if anyBoolDef(started["ok"], false) {
			out["message"] = "Local model activated and server started."
		}
		out["running"] = anyBoolDef(started["ok"], false)
	}
	return out
}

func (w *nativeVisionWorker) Activate(_ context.Context, homeDir string, enabled bool) (map[string]any, error) {
	return w.activateBundle(ResolveHomeDir(homeDir), enabled, true), nil
}

// ---- start / stop --------------------------------------------------------------

func nvVisionSuspended(home string) bool {
	raw, err := os.ReadFile(rmbJSONPath(home))
	if err != nil {
		return false
	}
	var st map[string]any
	return json.Unmarshal(raw, &st) == nil && anyBoolDef(st["vision_suspended"], false)
}

func (w *nativeVisionWorker) Start(ctx context.Context, homeDir string) (map[string]any, error) {
	return w.ensureServer(ctx, ResolveHomeDir(homeDir)), nil
}

func (w *nativeVisionWorker) Stop(_ context.Context, homeDir string) (map[string]any, error) {
	return w.stopServer(ResolveHomeDir(homeDir))
}

// ensureServer is service.ensure_server. (The extra MDL light tier the Python
// starts alongside is not ported.)
func (w *nativeVisionWorker) ensureServer(ctx context.Context, home string) map[string]any {
	if nvVisionSuspended(home) {
		return map[string]any{"ok": false, "skipped": true, "reason": "rmb_local_agent",
			"error": "Vision SmolVLM disabled while RMB is the local chat host"}
	}
	ngl := -1
	if vt, ok := asStringMap(LoadConfig(home)["vision"]); ok && vt != nil && vt["n_gpu_layers"] != nil {
		ngl = anyInt(vt["n_gpu_layers"], -1)
	}
	out, _ := w.startServer(ctx, home, ngl, nativeVisionStartW)
	return out
}

func nvLoopbackURL(raw string) bool {
	u, err := url.Parse(raw)
	if err != nil || u.Hostname() == "" || (u.Scheme != "http" && u.Scheme != "https") {
		return false
	}
	h := u.Hostname()
	if strings.EqualFold(h, "localhost") {
		return true
	}
	ip := net.ParseIP(h)
	return ip != nil && ip.IsLoopback()
}

// nvHealth is runtime._health: GET /models, then /v1/models, no redirects.
func nvHealth(base string, timeout time.Duration) bool {
	base = strings.TrimRight(strings.TrimSpace(base), "/")
	if base == "" || !nvLoopbackURL(base) {
		return false
	}
	client := &http.Client{Timeout: timeout,
		CheckRedirect: func(*http.Request, []*http.Request) error { return http.ErrUseLastResponse }}
	probe := func(u string) bool {
		req, err := http.NewRequest(http.MethodGet, u, nil)
		if err != nil {
			return false
		}
		req.Header.Set("User-Agent", "RemedyAI-vision/1.0")
		resp, err := client.Do(req)
		if err != nil {
			return false
		}
		resp.Body.Close()
		return resp.StatusCode >= 200 && resp.StatusCode < 300
	}
	if probe(base + "/models") {
		return true
	}
	alt := base + "/v1/models"
	if strings.HasSuffix(base, "/v1") {
		alt = base + "/models"
	}
	return probe(alt)
}

func (w *nativeVisionWorker) setProc(p *visionProc) {
	w.procMu.Lock()
	w.proc = p
	w.procMu.Unlock()
}

func (w *nativeVisionWorker) getProc() *visionProc {
	w.procMu.Lock()
	defer w.procMu.Unlock()
	return w.proc
}

func (w *nativeVisionWorker) ownPID() any {
	if p := w.getProc(); p != nil {
		if _, ex := p.Exited(); !ex {
			return int(p.PID)
		}
	}
	return nil
}

func nvPathStr(v any) string {
	if s := anyString(v); s != "" {
		return s
	}
	return "None"
}

// startServer is runtime.start_server.
func (w *nativeVisionWorker) startServer(ctx context.Context, home string, ngl int, wait time.Duration) (map[string]any, error) {
	w.startMu.Lock()
	defer w.startMu.Unlock()
	save := func(s map[string]any) { _ = writeJSONAtomic(visionJSONPath(home), s) }

	state := loadVisionJSON(home)
	// Retired or unknown model pins are rebound to the product default; old
	// weight files stay on disk.
	if mid := anyString(state["model_id"]); mid != "" && len(state) > 0 {
		known := false
		for _, m := range w.models {
			known = known || m.ID == mid
		}
		if !known {
			state["model_id"] = visionDefaultModelID
			delete(state, "model_path")
			delete(state, "mmproj_path")
			delete(state, "pid")
			save(state)
		}
	}
	if len(state) == 0 || !fileExists(anyString(state["model_path"])) {
		act := w.activateLocalBundle(home, true)
		if anyBoolDef(act["ok"], false) {
			state = loadVisionJSON(home)
		} else if len(state) == 0 {
			msg := anyString(act["error"])
			if msg == "" {
				msg = "Local model not activated (no vision.json / bundle)"
			}
			return map[string]any{"ok": false, "error": msg}, nil
		}
	}
	if len(state) == 0 {
		return map[string]any{"ok": false, "error": "Local model not ready (no vision.json)"}, nil
	}
	host := anyString(state["host"])
	if host == "" {
		host = visionDefaultHost
	}
	port := anyInt(state["port"], 0)
	if port == 0 {
		port = visionDefaultPort
	}
	base := anyString(state["base_url"])
	if base == "" {
		base = fmt.Sprintf("http://%s:%d/v1", host, port)
	}
	modelPath, mmprojPath := anyString(state["model_path"]), anyString(state["mmproj_path"])
	if modelPath == "" || !fileExists(modelPath) {
		return map[string]any{"ok": false, "error": "Model file missing: " + nvPathStr(state["model_path"])}, nil
	}
	if mmprojPath == "" || !fileExists(mmprojPath) {
		return map[string]any{"ok": false, "error": "mmproj file missing: " + nvPathStr(state["mmproj_path"])}, nil
	}
	if visionPortOpen(host, port) && nvHealth(base, 350*time.Millisecond) {
		w.NoteUse()
		w.ensureIdleWatcher(home)
		return map[string]any{"ok": true, "already_running": true, "base_url": base, "pid": w.ownPID()}, nil
	}

	binary := anyString(state["runtime_binary"])
	if binary == "" || !fileExists(binary) {
		binary = nativeVisionServerBinary(home)
	}
	if binary == "" {
		binary = w.runtimeBinaryFromBundle(anyString(state["runtime_id"]), "")
	}
	if binary == "" {
		return map[string]any{"ok": false, "error": "llama-server binary not found"}, nil
	}
	if abs, err := filepath.Abs(binary); err == nil {
		binary = abs
	}

	// Default port busy with something that is not our server: next free one.
	if visionPortOpen(host, port) && !nvHealth(base, 350*time.Millisecond) {
		for p := port + 1; p < port+20; p++ {
			if !visionPortOpen(host, p) {
				port = p
				base = fmt.Sprintf("http://%s:%d/v1", host, port)
				state["port"], state["base_url"] = port, base
				save(state)
				break
			}
		}
	}

	cfg := LoadConfig(home)
	ngl = nvMindGPULayers(home, cfg, ngl)
	cmd := nvBuildCommand(binary, modelPath, mmprojPath, host, port, ngl, cfg, nativeVisionHelp(home, binary))
	candidates := append([][]string{cmd}, nvCommandFallbacks(cmd)...)
	var lastErr string
	deadline := time.Now().Add(wait)
	for attempt, argv := range candidates {
		proc, err := nativeVisionSpawn(ctx, home, argv, filepath.Dir(binary))
		if err != nil {
			return map[string]any{"ok": false, "error": "Failed to start llama-server: " + err.Error()}, nil
		}
		w.setProc(proc)
		exitedEarly := false
		for time.Now().Before(deadline) {
			if ctx.Err() != nil {
				w.setProc(nil)
				_ = proc.Kill()
				return map[string]any{"ok": false, "error": "llama-server start cancelled"}, nil
			}
			cur := w.getProc()
			if cur == nil {
				return map[string]any{"ok": false,
					"error": "llama-server handle cleared during start (stopped concurrently)"}, nil
			}
			if code, ex := cur.Exited(); ex {
				lastErr = fmt.Sprintf("llama-server exited early (code %d)", code)
				exitedEarly = true
				break
			}
			if visionPortOpen(host, port) &&
				(nvHealth(base, 600*time.Millisecond) || time.Now().After(deadline.Add(-2*time.Second))) {
				state["pid"] = int(cur.PID)
				save(state)
				w.NoteUse()
				w.ensureIdleWatcher(home)
				return map[string]any{"ok": true, "already_running": false, "base_url": base, "pid": int(cur.PID)}, nil
			}
			time.Sleep(nativeVisionPoll)
		}
		if exitedEarly && attempt+1 < len(candidates) {
			continue // retry with a softer command line
		}
		break
	}
	if lastErr != "" {
		return map[string]any{"ok": false, "error": lastErr}, nil
	}
	return map[string]any{"ok": false,
		"error": fmt.Sprintf("llama-server did not become healthy within %.1fs", wait.Seconds())}, nil
}

func nvPIDAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	if _, err := os.Stat("/proc/self"); err == nil { // Linux
		_, err := os.Stat("/proc/" + strconv.Itoa(pid))
		return err == nil
	}
	p, err := os.FindProcess(pid) // Windows: fails when the process is gone
	if err != nil {
		return false
	}
	_ = p.Release()
	return true
}

// nativeVisionVerifyPID says whether pid is a llama-server (guards against
// killing a recycled PID). Linux reads /proc/<pid>/cmdline. Windows has no
// by-PID image-name query in the Zig host or in Go code here (the Zig
// processImagePath is private to the foreground-window call) and vision.json
// records only the PID, so it answers false: the record is reported stale and
// nothing is killed.
var nativeVisionVerifyPID = func(pid int) bool {
	raw, err := os.ReadFile("/proc/" + strconv.Itoa(pid) + "/cmdline")
	if err != nil {
		return false
	}
	t := strings.ToLower(strings.ReplaceAll(string(raw), "\x00", " "))
	return strings.Contains(t, "llama-server") || strings.Contains(t, "llama_server")
}

// stopServer is runtime.stop_server: our child plus any PID recorded in
// vision.json (a server a Python runtime started).
func (w *nativeVisionWorker) stopServer(home string) (map[string]any, error) {
	killed := false
	pids := []int{}
	own := -1
	w.procMu.Lock()
	proc := w.proc
	w.proc = nil
	w.procMu.Unlock()
	if proc != nil {
		own = int(proc.PID)
		if _, ex := proc.Exited(); !ex {
			pids = append(pids, own)
			killed = proc.Kill() == nil
		} else {
			_ = proc.Kill() // release the handle
		}
	}
	state := loadVisionJSON(home)
	recorded := -1
	if v, ok := state["pid"]; ok && v != nil {
		recorded = anyInt(v, -1)
		if recorded >= 0 {
			pids = append(pids, recorded)
		}
	}
	seen := map[int]bool{}
	list := []int{}
	stale := []int{}
	for _, pid := range pids {
		if seen[pid] {
			continue
		}
		seen[pid] = true
		list = append(list, pid)
		if pid == own || !nvPIDAlive(pid) {
			continue
		}
		if pid == recorded && !nativeVisionVerifyPID(pid) {
			stale = append(stale, pid)
			continue
		}
		if nativeVisionKill(uint32(pid)) == nil {
			killed = true
		}
	}
	if state["pid"] != nil {
		delete(state, "pid")
		_ = writeJSONAtomic(visionJSONPath(home), state)
	}
	out := map[string]any{"ok": true, "stopped": killed, "pids": list}
	if len(stale) > 0 {
		out["stale_records"] = stale
		out["message"] = "stale record: the PID in vision.json is alive but could not be verified as " +
			"the vision server, so it was not killed"
	}
	return out, nil
}

// ---- command line (mind_engine.build_mind_server_command) ----------------------

func nvKnob(cfg ConfigMap, key string) any {
	k := advancedByKey()[key]
	if v, ok := coerceAdvanced(k, resolveAdvanced(k, cfg)); ok {
		return v
	}
	return k.Default
}

func nvKnobBool(cfg ConfigMap, key string) bool { b, _ := nvKnob(cfg, key).(bool); return b }
func nvKnobStr(cfg ConfigMap, key string) string {
	s, _ := nvKnob(cfg, key).(string)
	return strings.ToLower(strings.TrimSpace(s))
}

func nvClamp(v, lo, hi, def int) int {
	if v == 0 {
		v = def
	}
	return max(lo, min(hi, v))
}

func nvHas(help []byte, needles ...string) bool {
	if len(help) == 0 {
		return true // cannot probe: assume the pinned build is new enough
	}
	low := strings.ToLower(string(help))
	for _, n := range needles {
		if strings.Contains(low, strings.ToLower(n)) {
			return true
		}
	}
	return false
}

func nvBuildCommand(binary, model, mmproj, host string, port, ngl int, cfg ConfigMap, help []byte) []string {
	slots := nvClamp(resolveAdvancedInt(cfg, "mind_slots"), 1, 8, 2)
	ctx := nvClamp(resolveAdvancedInt(cfg, "mind_ctx"), 2048, 32768, 8192)
	imageMax := nvClamp(resolveAdvancedInt(cfg, "mind_image_max_tokens"), 64, 4096, 1024)
	kv := nvKnobStr(cfg, "mind_kv_cache")
	switch kv {
	case "q8_0", "f16", "bf16", "f8_e4m3":
	default:
		kv = "q8_0"
	}
	cacheRAM := resolveAdvancedInt(cfg, "mind_cache_ram_mib")
	draftN := nvClamp(resolveAdvancedInt(cfg, "mind_spec_draft_n"), 1, 8, 3)

	cmd := []string{binary, "-m", model, "--mmproj", mmproj, "--host", host,
		"--port", strconv.Itoa(port), "--ctx-size", strconv.Itoa(ctx), "-ngl", strconv.Itoa(ngl)}
	if slots > 0 && nvHas(help, "--parallel", "-np") {
		cmd = append(cmd, "--parallel", strconv.Itoa(slots))
		if nvHas(help, "--kv-unified-per-slot") {
			cmd = append(cmd, "--kv-unified-per-slot", strconv.Itoa(ctx))
		}
	}
	if nvKnobBool(cfg, "mind_flash_attn") && nvHas(help, "--flash-attn", "-fa") {
		if len(help) == 0 || strings.Contains(string(help), "--flash-attn") {
			cmd = append(cmd, "--flash-attn", "on")
		} else {
			cmd = append(cmd, "-fa")
		}
	}
	if nvHas(help, "--jinja") {
		cmd = append(cmd, "--jinja")
	}
	if !nvKnobBool(cfg, "mind_thinking") && nvHas(help, "--chat-template-kwargs") {
		cmd = append(cmd, "--chat-template-kwargs", `{"enable_thinking": false}`)
	}
	if nvKnobBool(cfg, "mind_cache_prompt") {
		if nvHas(help, "--cache-prompt") {
			cmd = append(cmd, "--cache-prompt")
		}
		if cacheRAM != 0 && nvHas(help, "--cache-ram") {
			cmd = append(cmd, "--cache-ram", strconv.Itoa(cacheRAM))
		}
		if nvHas(help, "--cache-idle-slots") {
			cmd = append(cmd, "--cache-idle-slots")
		}
	}
	if nvKnobBool(cfg, "mind_mmap") && nvHas(help, "--mmap") {
		cmd = append(cmd, "--mmap")
	}
	if nvHas(help, "--cache-type-k") {
		cmd = append(cmd, "--cache-type-k", kv, "--cache-type-v", kv)
	}
	if nvHas(help, "--image-max-tokens") {
		cmd = append(cmd, "--image-max-tokens", strconv.Itoa(imageMax))
	}
	if spec := nvSpecType(model, help, cfg); spec != "" && nvHas(help, "--spec-type") {
		cmd = append(cmd, "--spec-type", spec)
		if nvHas(help, "--spec-draft-n-max") {
			cmd = append(cmd, "--spec-draft-n-max", strconv.Itoa(draftN))
		}
	}
	return cmd
}

// nvSpecType is resolve_spec_type; the pinned model has an MTP head (catalog
// has_mtp), so auto prefers draft-mtp.
func nvSpecType(model string, help []byte, cfg ConfigMap) string {
	switch nvKnobStr(cfg, "mind_spec") {
	case "off", "none", "false", "0":
		return ""
	case "draft-mtp", "mtp":
		return "draft-mtp"
	case "ngram-mod", "ngram":
		return "ngram-mod"
	}
	hasMTP := strings.Contains(strings.ToLower(filepath.Base(model)), "mtp") || visionModels[0].HasMTP
	if hasMTP && nvHas(help, "draft-mtp") {
		return "draft-mtp"
	}
	if nvHas(help, "ngram-mod") {
		return "ngram-mod"
	}
	return ""
}

// nvCommandFallbacks is command_fallbacks: softer command lines for builds
// that exit early on speculative / flash-attn / quantised-KV flags.
func nvCommandFallbacks(cmd []string) [][]string {
	valued := map[string]bool{"--spec-type": true, "--spec-draft-n-max": true, "--cache-type-k": true,
		"--cache-type-v": true, "--flash-attn": true}
	without := func(flags ...string) []string {
		set := map[string]bool{}
		for _, f := range flags {
			set[f] = true
		}
		var out []string
		skip := false
		for i, tok := range cmd {
			if skip {
				skip = false
				continue
			}
			if set[tok] {
				if valued[tok] && i+1 < len(cmd) && !strings.HasPrefix(cmd[i+1], "-") {
					skip = true
				}
				continue
			}
			out = append(out, tok)
		}
		return out
	}
	has := func(f ...string) bool {
		for _, t := range cmd {
			for _, x := range f {
				if t == x {
					return true
				}
			}
		}
		return false
	}
	var variants [][]string
	if has("--spec-type") {
		variants = append(variants, without("--spec-type", "--spec-draft-n-max"))
		soft := append(without("--spec-type", "--spec-draft-n-max"), "--spec-type", "ngram-mod")
		variants = append([][]string{soft}, variants...)
	}
	if has("--flash-attn", "-fa") {
		variants = append(variants, without("--flash-attn", "-fa"))
	}
	if has("--cache-type-k") {
		variants = append(variants, without("--cache-type-k", "--cache-type-v"))
	}
	seen := map[string]bool{strings.Join(cmd, "\x00"): true}
	var uniq [][]string
	for _, v := range variants {
		if k := strings.Join(v, "\x00"); !seen[k] {
			seen[k] = true
			uniq = append(uniq, v)
		}
	}
	return uniq
}
