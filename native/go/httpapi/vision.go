package httpapi

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"time"
)

// VisionWorker runs ML install/runtime ops (Python vision lane).
// Nil → status/catalog still work; activate/install/start/stop return 503.
type VisionWorker interface {
	Activate(ctx context.Context, homeDir string, enabled bool) (map[string]any, error)
	Install(ctx context.Context, homeDir, modelID, runtimeID string, preferCUDA bool) (map[string]any, error)
	CancelInstall(ctx context.Context, homeDir string) (map[string]any, error)
	ReinstallRuntime(ctx context.Context, homeDir string, preferCUDA bool) (map[string]any, error)
	Uninstall(ctx context.Context, homeDir string, keepModels bool) (map[string]any, error)
	Start(ctx context.Context, homeDir string) (map[string]any, error)
	Stop(ctx context.Context, homeDir string) (map[string]any, error)
	// Progress returns in-flight install snapshot; nil/empty → idle.
	Progress(ctx context.Context, homeDir string) map[string]any
}

func visionHome(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), "vision")
}

func visionJSONPath(homeDir string) string {
	return filepath.Join(visionHome(homeDir), "vision.json")
}

func visionModelsDir(homeDir, modelID string) string {
	return filepath.Join(visionHome(homeDir), "models", modelID)
}

func visionRuntimeDir(homeDir string) string {
	return filepath.Join(visionHome(homeDir), "runtime")
}

func loadVisionJSON(homeDir string) map[string]any {
	raw, err := os.ReadFile(visionJSONPath(homeDir))
	if err != nil {
		return map[string]any{}
	}
	var parsed map[string]any
	if json.Unmarshal(raw, &parsed) != nil || parsed == nil {
		return map[string]any{}
	}
	return parsed
}

func visionSectionFromConfig(cfg ConfigMap) map[string]any {
	raw, _ := asStringMap(cfg["vision"])
	if raw == nil {
		raw = map[string]any{}
	}
	host := strings.TrimSpace(anyString(raw["host"]))
	if host == "" {
		host = visionDefaultHost
	}
	port := anyInt(raw["port"], visionDefaultPort)
	mid := strings.TrimSpace(anyString(raw["model_id"]))
	if mid == "" {
		mid = visionDefaultModelID
	}
	switch strings.ToLower(mid) {
	case "qwen2.5-vl-3b", "qwen2.5-vl", "qwen2.5vl-3b", "qwen-vl-3b", "qwen2-vl", "qwen-vl":
		mid = visionDefaultModelID
	}
	base := strings.TrimSpace(anyString(raw["base_url"]))
	if base == "" {
		base = fmt.Sprintf("http://%s:%d/v1", host, port)
	}
	rid := strings.TrimSpace(anyString(raw["runtime_id"]))
	if rid == "" {
		rid = visionDefaultRuntime(false)
	}
	return map[string]any{
		"enabled":      anyBoolDef(raw["enabled"], true),
		"model_id":     mid,
		"host":         host,
		"port":         port,
		"base_url":     base,
		"auto_start":   anyBoolDef(raw["auto_start"], true),
		"idle_stop_s":  anyInt(raw["idle_stop_s"], 600),
		"runtime_id":   rid,
		"force_decode": anyBoolDef(raw["force_decode"], false),
	}
}

func visionModelFilesPresent(homeDir, modelID string) bool {
	spec := visionModelByID(modelID)
	dir := visionModelsDir(homeDir, spec.ID)
	return fileExists(filepath.Join(dir, spec.ModelFile)) &&
		fileExists(filepath.Join(dir, spec.MMProjFile))
}

func visionRuntimeBinary(homeDir string) string {
	root := visionRuntimeDir(homeDir)
	for _, name := range []string{"llama-server.exe", "llama-server"} {
		p := filepath.Join(root, name)
		if fileExists(p) {
			return p
		}
	}
	var found string
	_ = filepath.WalkDir(root, func(path string, d os.DirEntry, err error) error {
		if err != nil || d == nil || d.IsDir() {
			return nil
		}
		base := strings.ToLower(d.Name())
		if base == "llama-server" || base == "llama-server.exe" {
			found = path
			return filepath.SkipAll
		}
		return nil
	})
	return found
}

func visionInstalled(homeDir, modelID string) bool {
	return visionModelFilesPresent(homeDir, modelID) && visionRuntimeBinary(homeDir) != ""
}

func visionPortOpen(host string, port int) bool {
	addr := net.JoinHostPort(host, strconv.Itoa(port))
	conn, err := net.DialTimeout("tcp", addr, 400*time.Millisecond)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

func idleVisionProgress() map[string]any {
	return map[string]any{
		"phase": "idle", "message": "", "bytes_done": 0, "bytes_total": 0,
		"current_file": "", "error": nil, "model_id": nil, "runtime_id": nil,
		"cancellable": false, "resumed": false,
	}
}

func (s *Server) visionProgressSnapshot() map[string]any {
	if s.vision != nil {
		if p := s.vision.Progress(context.Background(), ResolveHomeDir(s.homeDir)); p != nil {
			return p
		}
	}
	return idleVisionProgress()
}

func patchVisionConfigEnabled(homeDir string, enabled bool, modelID, runtimeID string) {
	path := FindConfigPath(homeDir)
	if path == "" {
		path = DefaultConfigPath(homeDir)
		if path == "" {
			return
		}
	}
	cfg := LoadConfig(homeDir)
	visionTbl, _ := asStringMap(cfg["vision"])
	if visionTbl == nil {
		visionTbl = map[string]any{}
	}
	visionTbl["enabled"] = enabled
	if mid := strings.TrimSpace(modelID); mid != "" {
		visionTbl["model_id"] = mid
	} else {
		visionTbl["model_id"] = visionDefaultModelID
	}
	if rid := strings.TrimSpace(runtimeID); rid != "" {
		visionTbl["runtime_id"] = rid
	}
	cfg["vision"] = visionTbl
	_ = WriteConfig(path, cfg)
	InvalidateConfigCache()
}

func (s *Server) visionStatusPayload(full bool) map[string]any {
	home := ResolveHomeDir(s.homeDir)
	cfg := LoadConfig(s.homeDir)
	vcfg := visionSectionFromConfig(cfg)
	side := loadVisionJSON(home)
	mid := strings.TrimSpace(anyString(side["model_id"]))
	if mid == "" {
		mid = anyString(vcfg["model_id"])
	}
	if mid == "" {
		mid = visionDefaultModelID
	}
	spec := visionModelByID(mid)
	mid = spec.ID

	installed := visionInstalled(home, mid)
	host := strings.TrimSpace(anyString(side["host"]))
	if host == "" {
		host = anyString(vcfg["host"])
	}
	if host == "" {
		host = visionDefaultHost
	}
	port := anyInt(side["port"], 0)
	if port <= 0 {
		port = anyInt(vcfg["port"], visionDefaultPort)
	}
	base := strings.TrimSpace(anyString(side["base_url"]))
	if base == "" {
		base = anyString(vcfg["base_url"])
	}
	if base == "" {
		base = fmt.Sprintf("http://%s:%d/v1", host, port)
	}
	rid := strings.TrimSpace(anyString(side["runtime_id"]))
	if rid == "" {
		rid = anyString(vcfg["runtime_id"])
	}
	if rid == "" {
		rid = visionDefaultRuntime(false)
	}
	enabled := anyBoolDef(vcfg["enabled"], false) || anyBoolDef(side["enabled"], false)
	running := installed && visionPortOpen(host, port)
	ready := enabled && installed
	progress := s.visionProgressSnapshot()

	var hint any
	if !ready {
		if installed {
			hint = fmt.Sprintf(
				"Local model is off. Enable Vision & nano swarm in Settings — %s starts with Remedy when ready.",
				spec.Name,
			)
		} else {
			hint = fmt.Sprintf(
				"Local vision is downloading with Remedy — pinned %s (one-time; then starts with Remedy).",
				spec.Name,
			)
		}
	}

	out := map[string]any{
		"enabled": enabled, "installed": installed, "running": running, "ready": ready,
		"force_decode": anyBoolDef(vcfg["force_decode"], false),
		"model_id":     mid,
		"model":        spec.toPublic(),
		"backend":      firstNonEmpty(anyString(side["backend"]), "llama_server"),
		"base_url":     base,
		"port":         port,
		"host":         host,
		"runtime_version": side["runtime_version"],
		"runtime_id":   rid,
		"progress":     progress,
		"not_ready_hint": hint,
		"bundled":      anyBoolDef(side["bundled"], false),
		"local_roles":  append([]string(nil), visionLocalRoles...),
		"bundle_policy": visionBundlePolicy,
		"auto_start":   anyBoolDef(vcfg["auto_start"], true),
		"delivery":     "first_run_download",
	}
	if full {
		out["health"] = map[string]any{
			"ram_gb": nil, "disk_free_gb": nil,
			"install_need_gb": float64(spec.approxDownloadBytes()) / (1024 * 1024 * 1024),
			"min_ram_gb":     spec.MinRAMGB,
			"nvidia_detected": false,
			"runtime_id":     rid,
			"cpu_runtime":    !strings.Contains(strings.ToLower(rid), "cuda") &&
				!strings.Contains(strings.ToLower(rid), "vulkan"),
			"warnings": []any{},
		}
		out["warnings"] = []any{}
		out["catalog"] = visionCatalogPublic()
	} else {
		out["health"] = nil
		out["warnings"] = []any{}
		out["catalog"] = nil
	}
	return out
}

func (s *Server) refuseVisionWorker(w http.ResponseWriter) bool {
	if s.vision != nil {
		return false
	}
	writeJSON(w, http.StatusServiceUnavailable, map[string]any{
		"ok":    false,
		"error": "Vision worker is not attached to remedy-runtime.",
		"hint":  "Wire a VisionWorker (Python vision lane) or use the Python sidecar.",
	})
	return true
}

func (s *Server) handleVisionStatus(w http.ResponseWriter, r *http.Request) {
	full := false
	switch strings.ToLower(strings.TrimSpace(r.URL.Query().Get("full"))) {
	case "1", "true", "yes", "on":
		full = true
	}
	writeJSON(w, http.StatusOK, s.visionStatusPayload(full))
}

func (s *Server) handleVisionCatalog(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, visionCatalogPublic())
}

func (s *Server) handleVisionActivate(w http.ResponseWriter, r *http.Request) {
	if s.refuseVisionWorker(w) {
		return
	}
	home := ResolveHomeDir(s.homeDir)
	patchVisionConfigEnabled(s.homeDir, true, visionDefaultModelID, "")
	out, err := s.vision.Activate(r.Context(), home, true)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleVisionInstall(w http.ResponseWriter, r *http.Request) {
	if s.refuseVisionWorker(w) {
		return
	}
	var body struct {
		ModelID    string `json:"model_id"`
		RuntimeID  string `json:"runtime_id"`
		PreferCUDA bool   `json:"prefer_cuda"`
	}
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	_ = dec.Decode(&body)
	home := ResolveHomeDir(s.homeDir)
	rid := strings.TrimSpace(body.RuntimeID)
	if rid == "" && body.PreferCUDA {
		rid = visionDefaultRuntime(true)
	}
	patchVisionConfigEnabled(s.homeDir, true, visionDefaultModelID, rid)
	out, err := s.vision.Install(r.Context(), home, visionDefaultModelID, rid, body.PreferCUDA)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleVisionInstallCancel(w http.ResponseWriter, r *http.Request) {
	if s.refuseVisionWorker(w) {
		return
	}
	out, err := s.vision.CancelInstall(r.Context(), ResolveHomeDir(s.homeDir))
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleVisionReinstallRuntime(w http.ResponseWriter, r *http.Request) {
	if s.refuseVisionWorker(w) {
		return
	}
	var body struct {
		PreferCUDA *bool `json:"prefer_cuda"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body)
	prefer := true
	if body.PreferCUDA != nil {
		prefer = *body.PreferCUDA
	}
	out, err := s.vision.ReinstallRuntime(r.Context(), ResolveHomeDir(s.homeDir), prefer)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleVisionUninstall(w http.ResponseWriter, r *http.Request) {
	if s.refuseVisionWorker(w) {
		return
	}
	var body struct {
		KeepModels bool `json:"keep_models"`
	}
	_ = json.NewDecoder(io.LimitReader(r.Body, 1<<20)).Decode(&body)
	out, err := s.vision.Uninstall(r.Context(), ResolveHomeDir(s.homeDir), body.KeepModels)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	patchVisionConfigEnabled(s.homeDir, false, "", "")
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleVisionStart(w http.ResponseWriter, r *http.Request) {
	if s.refuseVisionWorker(w) {
		return
	}
	out, err := s.vision.Start(r.Context(), ResolveHomeDir(s.homeDir))
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleVisionStop(w http.ResponseWriter, r *http.Request) {
	if s.refuseVisionWorker(w) {
		return
	}
	out, err := s.vision.Stop(r.Context(), ResolveHomeDir(s.homeDir))
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}
