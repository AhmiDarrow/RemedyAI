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

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	rmbDefaultHost = "127.0.0.1"
	rmbDefaultPort = 8787
	rmbDefaultCtx  = 8192
)

// RmbController starts/stops llama-server and applies live process restarts.
// Nil → disk settings still save; start/stop/use return a clear 503.
// Production wires a Python-backed (or Zig) process supervisor here.
type RmbController interface {
	Start(ctx context.Context, homeDir string) (map[string]any, error)
	Stop(ctx context.Context, homeDir string) (map[string]any, error)
	ApplyLive(ctx context.Context, homeDir string, state map[string]any) (map[string]any, error)
}

func rmbHome(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), "rmb")
}

func rmbJSONPath(homeDir string) string {
	return filepath.Join(rmbHome(homeDir), "rmb.json")
}

func rmbModelsDir(homeDir string) string {
	return filepath.Join(rmbHome(homeDir), "models")
}

func defaultRmbState() map[string]any {
	return map[string]any{
		"enabled": false, "auto_start": false,
		"host": rmbDefaultHost, "port": rmbDefaultPort,
		"base_url":       fmt.Sprintf("http://%s:%d/v1", rmbDefaultHost, rmbDefaultPort),
		"model_id":       "qwen25-coder-7b",
		"model_path":     "",
		"runtime_binary": "", "runtime_id": "",
		"n_gpu_layers": -1, "n_cpu_moe": 0,
		"ctx_size": rmbDefaultCtx, "threads": 0, "parallel": 1,
		"flash_attn": true, "thinking": "on", "reasoning_budget": -1,
		"enable_mtp": true, "spec_draft_n_max": 0, "n_gpu_layers_draft": 0,
		"model_draft": "", "chat_template": "",
		"temperature": 0.8, "top_p": 0.95, "top_k": 40, "min_p": 0.05,
		"repeat_penalty": 1.1, "repeat_last_n": 64, "seed": -1,
		"batch_size": 2048, "ubatch_size": 512, "mmproj": "",
		"use_jinja": true, "use_jinja_owner": false,
		"rope_freq_scale": 0.0, "rope_freq_base": 0.0,
		"typical_p": 0.0, "tfs_z": 0.0, "mirostat": 0,
		"mirostat_tau": 0.0, "mirostat_eta": 0.0,
		"presence_penalty": 0.0, "frequency_penalty": 0.0,
		"main_gpu": 0, "threads_batch": 0, "tensor_split": "",
		"samplers": "", "rope_scaling": "",
		"yarn_orig_ctx": 0, "yarn_factor": 0.0, "yarn_beta_fast": 0.0, "yarn_beta_slow": 0.0,
		"no_kv_offload": false, "mlock": false, "no_mmap": false, "cache_type": "",
		"dry_multiplier": 0.0, "dry_base": 1.75, "dry_allowed_length": 2, "dry_penalty_last_n": -1,
		"xtc_probability": 0.0, "xtc_threshold": 0.1, "cache_reuse": 256,
		"profile": "autofit", "autofit": true, "autofit_locked": false,
		"last_autofit": nil, "last_good_fit": nil, "pid": nil,
		"vision_suspended": false, "user_stopped": false,
	}
}

func loadRmbJSON(homeDir string) map[string]any {
	raw, err := os.ReadFile(rmbJSONPath(homeDir))
	if err != nil {
		return map[string]any{}
	}
	var parsed map[string]any
	if json.Unmarshal(raw, &parsed) != nil || parsed == nil {
		return map[string]any{}
	}
	return parsed
}

func mergeRmbState(existing map[string]any) map[string]any {
	base := defaultRmbState()
	for k, v := range existing {
		if v != nil {
			base[k] = v
		}
	}
	host := anyString(base["host"])
	if host == "" {
		host = rmbDefaultHost
	}
	port := anyInt(base["port"], rmbDefaultPort)
	base["host"] = host
	base["port"] = port
	base["base_url"] = fmt.Sprintf("http://%s:%d/v1", host, port)
	return base
}

func saveRmbJSON(homeDir string, state map[string]any) error {
	if err := os.MkdirAll(rmbHome(homeDir), 0o700); err != nil {
		return err
	}
	data, err := json.MarshalIndent(state, "", "  ")
	if err != nil {
		return err
	}
	data = append(data, '\n')
	return secret.WriteFileAtomic(rmbJSONPath(homeDir), data, 0o600)
}

func anyInt(v any, def int) int {
	switch t := v.(type) {
	case int:
		return t
	case int64:
		return int(t)
	case float64:
		return int(t)
	case json.Number:
		if i, err := t.Int64(); err == nil {
			return int(i)
		}
		if f, err := t.Float64(); err == nil {
			return int(f)
		}
	case string:
		if i, err := strconv.Atoi(strings.TrimSpace(t)); err == nil {
			return i
		}
	}
	return def
}

func resolveRmbModelPath(state map[string]any, homeDir string) string {
	if mp := strings.TrimSpace(anyString(state["model_path"])); mp != "" && fileExists(mp) {
		return mp
	}
	mid := anyString(state["model_id"])
	spec := rmbModelByID(mid)
	candidates := []string{
		filepath.Join(rmbModelsDir(homeDir), spec.Filename),
	}
	_ = os.MkdirAll(rmbModelsDir(homeDir), 0o700)
	ents, _ := os.ReadDir(rmbModelsDir(homeDir))
	for _, e := range ents {
		if e.IsDir() {
			continue
		}
		name := e.Name()
		if !strings.HasSuffix(strings.ToLower(name), ".gguf") {
			continue
		}
		full := filepath.Join(rmbModelsDir(homeDir), name)
		if catalogIDFromHint(name) == spec.ID || strings.EqualFold(name, spec.Filename) {
			return full
		}
		candidates = append(candidates, full)
	}
	for _, c := range candidates {
		if fileExists(c) {
			return c
		}
	}
	return ""
}

func findLlamaBinary(state map[string]any, homeDir string) string {
	if rb := strings.TrimSpace(anyString(state["runtime_binary"])); rb != "" && fileExists(rb) {
		return rb
	}
	if env := strings.TrimSpace(os.Getenv("REMEDY_LLAMA_SERVER")); env != "" && fileExists(env) {
		return env
	}
	// Common install locations under the remedy home.
	home := ResolveHomeDir(homeDir)
	names := []string{"llama-server.exe", "llama-server"}
	roots := []string{
		filepath.Join(home, "rmb", "runtime"),
		filepath.Join(home, "runtime"),
		filepath.Join(home, "vision", "runtime"),
	}
	for _, root := range roots {
		for _, name := range names {
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
				if fileExists(path) {
					found = path
					return filepath.SkipAll
				}
			}
			return nil
		})
		if found != "" {
			return found
		}
	}
	return ""
}

func discoverGGUFs(homeDir string) []map[string]any {
	dir := rmbModelsDir(homeDir)
	_ = os.MkdirAll(dir, 0o700)
	ents, err := os.ReadDir(dir)
	if err != nil {
		return []map[string]any{}
	}
	out := make([]map[string]any, 0)
	for _, e := range ents {
		if e.IsDir() {
			continue
		}
		name := e.Name()
		if !strings.HasSuffix(strings.ToLower(name), ".gguf") {
			continue
		}
		full := filepath.Join(dir, name)
		st, err := os.Stat(full)
		sizeGB := 0.0
		if err == nil {
			sizeGB = float64(st.Size()) / (1024 * 1024 * 1024)
			sizeGB = float64(int(sizeGB*100+0.5)) / 100
		}
		out = append(out, map[string]any{
			"path": full, "name": name, "size_gb": sizeGB,
		})
		if len(out) >= 24 {
			break
		}
	}
	return out
}

func rmbPortOpen(host string, port int) bool {
	addr := net.JoinHostPort(host, strconv.Itoa(port))
	conn, err := net.DialTimeout("tcp", addr, 400*time.Millisecond)
	if err != nil {
		return false
	}
	_ = conn.Close()
	return true
}

func rmbHealthOK(baseURL string) (ready bool, loading bool) {
	base := strings.TrimRight(baseURL, "/")
	if base == "" {
		return false, false
	}
	root := base
	if strings.HasSuffix(root, "/v1") {
		root = root[:len(root)-3]
	}
	client := &http.Client{Timeout: 900 * time.Millisecond}
	urls := []string{root + "/health"}
	if strings.HasSuffix(base, "/v1") {
		urls = append(urls, base+"/models")
	} else {
		urls = append(urls, base+"/v1/models")
	}
	for i, u := range urls {
		req, err := http.NewRequest(http.MethodGet, u, nil)
		if err != nil {
			continue
		}
		req.Header.Set("User-Agent", "RemedyAI-RMB/1.0")
		resp, err := client.Do(req)
		if err != nil {
			return false, false
		}
		body, _ := io.ReadAll(io.LimitReader(resp.Body, 256))
		_ = resp.Body.Close()
		if resp.StatusCode == http.StatusServiceUnavailable {
			return false, true
		}
		if resp.StatusCode == http.StatusNotFound && i == 0 {
			continue
		}
		if resp.StatusCode >= 200 && resp.StatusCode < 300 {
			low := strings.ToLower(string(body))
			if strings.Contains(low, "loading") && !strings.Contains(low, "ok") {
				return false, true
			}
			return true, false
		}
		return false, false
	}
	return false, false
}

func normalizeThinking(v any) string {
	s := strings.ToLower(strings.TrimSpace(anyString(v)))
	switch s {
	case "0", "false", "no", "off", "disabled", "disable":
		return "off"
	case "1", "true", "yes", "on", "enabled", "enable", "":
		return "on"
	default:
		if b, ok := v.(bool); ok {
			if b {
				return "on"
			}
			return "off"
		}
		return "on"
	}
}

func (s *Server) rmbStatusPayload() map[string]any {
	home := ResolveHomeDir(s.homeDir)
	disk := loadRmbJSON(home)
	state := mergeRmbState(disk)
	host := anyString(state["host"])
	port := anyInt(state["port"], rmbDefaultPort)
	base := anyString(state["base_url"])
	modelPath := resolveRmbModelPath(state, home)
	binary := findLlamaBinary(state, home)
	portUp := rmbPortOpen(host, port)
	ready, loading := false, false
	if portUp {
		ready, loading = rmbHealthOK(base)
	}
	starting := portUp && !ready
	running := ready || starting
	spec := rmbModelByID(anyString(state["model_id"]))
	chatStem := ""
	if modelPath != "" {
		chatStem = strings.TrimSuffix(filepath.Base(modelPath), filepath.Ext(modelPath))
	} else {
		chatStem = anyString(state["model_id"])
	}
	modelPublic := spec.toPublic()
	if modelPath != "" {
		modelPublic["id"] = chatStem
		modelPublic["filename"] = filepath.Base(modelPath)
		modelPublic["name"] = chatStem
		modelPublic["path"] = modelPath
	}
	installed := modelPath != "" && binary != ""
	var hint any
	if !ready {
		switch {
		case modelPath == "":
			hint = "Place any GGUF in ~/.remedy/rmb/models/ and click Start RMB"
		case binary == "":
			hint = "Install llama-server (Local vision runtime once) then Start RMB"
		case loading:
			hint = "Loading model…"
		case starting:
			hint = "Starting…"
		default:
			hint = "Start RMB to load the model"
		}
	}
	userStopped := anyBoolDef(state["user_stopped"], false)
	return map[string]any{
		"ok": true, "brand": "RMB", "brand_full": "Remedy Muscle Bridge",
		"engine_brand": "llama.cpp",
		"enabled":      anyBoolDef(state["enabled"], false),
		"auto_start":   anyBoolDef(state["auto_start"], false),
		"installed":    installed,
		"running":      running,
		"ready":        ready,
		"starting":     starting && !ready,
		"loading":      loading,
		"loading_for_s": 0,
		"loading_stalled": false,
		"pid":          state["pid"],
		"managed_child": false,
		"watchdog":     false,
		"user_stopped": userStopped,
		"base_url":     base,
		"host":         host,
		"port":         port,
		"model_id":     chatStem,
		"model":        modelPublic,
		"chat_model":   chatStem,
		"llm_model":    chatStem,
		"model_path": func() any {
			if modelPath == "" {
				return nil
			}
			return modelPath
		}(),
		"model_present":   modelPath != "",
		"runtime_binary":  nullIfEmpty(binary),
		"runtime_present": binary != "",
		"ctx_size":        anyInt(state["ctx_size"], rmbDefaultCtx),
		"n_gpu_layers":    state["n_gpu_layers"],
		"profile":         anyString(state["profile"]),
		"engine":          rmbEnginePublic(state),
		"catalog":         rmbCatalogPublic(),
		"discovered_ggufs": discoverGGUFs(home),
		"not_ready_hint":  hint,
		"local_agent_mode": running,
		"skips_vision_stack": running || anyBoolDef(state["vision_suspended"], false),
		"vision_suspended":  running || anyBoolDef(state["vision_suspended"], false),
		"endless_session": map[string]any{
			"harness_min_pct": 0.55, "harness_max_pct": 0.78,
			"ctx_size": anyInt(state["ctx_size"], rmbDefaultCtx),
			"silent_context": true,
			"note": "Context is automatic — Session Brief + harness keep long sessions " +
				"alive without user-facing compress talk. " +
				"SmolVLM is unloaded while RMB is running.",
		},
	}
}

func nullIfEmpty(s string) any {
	if s == "" {
		return nil
	}
	return s
}

func rmbEnginePublic(state map[string]any) map[string]any {
	return map[string]any{
		"threads": anyInt(state["threads"], 0), "parallel": anyInt(state["parallel"], 1),
		"flash_attn": anyBoolDef(state["flash_attn"], true),
		"temperature": state["temperature"], "top_p": state["top_p"], "top_k": state["top_k"],
		"min_p": state["min_p"], "repeat_penalty": state["repeat_penalty"],
		"repeat_last_n": state["repeat_last_n"], "seed": state["seed"],
		"batch_size": state["batch_size"], "ubatch_size": state["ubatch_size"],
		"mmproj": anyString(state["mmproj"]), "chat_template": anyString(state["chat_template"]),
		"use_jinja": anyBoolDef(state["use_jinja"], true),
		"use_jinja_owner": anyBoolDef(state["use_jinja_owner"], false),
		"rope_freq_scale": state["rope_freq_scale"], "rope_freq_base": state["rope_freq_base"],
		"mlock": anyBoolDef(state["mlock"], false), "no_mmap": anyBoolDef(state["no_mmap"], false),
		"cache_type": anyString(state["cache_type"]),
		"typical_p": state["typical_p"], "tfs_z": state["tfs_z"], "mirostat": state["mirostat"],
		"mirostat_tau": state["mirostat_tau"], "mirostat_eta": state["mirostat_eta"],
		"presence_penalty": state["presence_penalty"], "frequency_penalty": state["frequency_penalty"],
		"main_gpu": state["main_gpu"], "threads_batch": state["threads_batch"],
		"tensor_split": anyString(state["tensor_split"]), "samplers": anyString(state["samplers"]),
		"rope_scaling": anyString(state["rope_scaling"]),
		"yarn_orig_ctx": state["yarn_orig_ctx"], "yarn_factor": state["yarn_factor"],
		"yarn_beta_fast": state["yarn_beta_fast"], "yarn_beta_slow": state["yarn_beta_slow"],
		"no_kv_offload": anyBoolDef(state["no_kv_offload"], false),
		"dry_multiplier": state["dry_multiplier"], "dry_base": state["dry_base"],
		"dry_allowed_length": state["dry_allowed_length"], "dry_penalty_last_n": state["dry_penalty_last_n"],
		"xtc_probability": state["xtc_probability"], "xtc_threshold": state["xtc_threshold"],
		"cache_reuse": state["cache_reuse"],
		"thinking": normalizeThinking(state["thinking"]),
		"reasoning_budget": state["reasoning_budget"],
		"enable_mtp": anyBoolDef(state["enable_mtp"], true),
		"spec_draft_n_max": state["spec_draft_n_max"],
		"n_cpu_moe": state["n_cpu_moe"],
		"n_gpu_layers_draft": state["n_gpu_layers_draft"],
		"model_draft": anyString(state["model_draft"]),
	}
}

func (s *Server) handleRmbStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.rmbStatusPayload())
}

func (s *Server) handleRmbCatalog(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, rmbCatalogPublic())
}

func (s *Server) refuseIfStreaming(w http.ResponseWriter) bool {
	if s.claims != nil && s.claims.AnyActive() {
		writeJSON(w, http.StatusConflict, map[string]string{
			"detail": "Cannot reload RMB while a session is streaming. Stop the current turn first.",
		})
		return true
	}
	return false
}

func (s *Server) handleRmbStart(w http.ResponseWriter, r *http.Request) {
	if s.refuseIfStreaming(w) {
		return
	}
	home := ResolveHomeDir(s.homeDir)
	state := mergeRmbState(loadRmbJSON(home))
	state["enabled"] = true
	_ = saveRmbJSON(home, state)
	if s.rmb == nil {
		writeJSON(w, http.StatusServiceUnavailable, map[string]any{
			"ok":    false,
			"error": "RMB process controller is not attached to remedy-runtime.",
			"hint":  "Wire an RmbController (Python worker) or use the Python sidecar.",
		})
		return
	}
	out, err := s.rmb.Start(r.Context(), home)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}

func (s *Server) handleRmbStop(w http.ResponseWriter, r *http.Request) {
	home := ResolveHomeDir(s.homeDir)
	state := mergeRmbState(loadRmbJSON(home))
	state["user_stopped"] = true
	state["auto_start"] = false
	_ = saveRmbJSON(home, state)
	if s.rmb == nil {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":    true,
			"note":  "Marked stopped on disk; no process controller attached.",
			"stopped": false,
		})
		return
	}
	out, err := s.rmb.Stop(r.Context(), home)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if out == nil {
		out = map[string]any{"ok": true}
	}
	writeJSON(w, http.StatusOK, out)
}

var rmbSettingsKeys = []string{
	"enabled", "auto_start", "host", "port", "model_id", "model_path",
	"runtime_binary", "runtime_id", "n_gpu_layers", "ctx_size", "threads",
	"parallel", "flash_attn", "profile", "temperature", "top_p", "top_k",
	"min_p", "repeat_penalty", "repeat_last_n", "seed", "batch_size",
	"ubatch_size", "mmproj", "chat_template", "use_jinja", "rope_freq_scale",
	"rope_freq_base", "mlock", "no_mmap", "cache_type", "typical_p", "tfs_z",
	"mirostat", "mirostat_tau", "mirostat_eta", "presence_penalty",
	"frequency_penalty", "main_gpu", "threads_batch", "tensor_split",
	"samplers", "rope_scaling", "yarn_orig_ctx", "yarn_factor", "yarn_beta_fast",
	"yarn_beta_slow", "no_kv_offload", "dry_multiplier", "dry_base",
	"dry_allowed_length", "dry_penalty_last_n", "xtc_probability",
	"xtc_threshold", "cache_reuse", "thinking", "reasoning_budget",
	"enable_mtp", "spec_draft_n_max", "n_cpu_moe", "n_gpu_layers_draft",
	"model_draft",
}

func (s *Server) applyRmbSettingsPatch(home string, patch map[string]any) (map[string]any, error) {
	state := mergeRmbState(loadRmbJSON(home))
	keySet := map[string]struct{}{}
	for _, k := range rmbSettingsKeys {
		keySet[k] = struct{}{}
	}
	for k, v := range patch {
		if _, ok := keySet[k]; !ok {
			continue
		}
		switch k {
		case "ctx_size":
			n := anyInt(v, rmbDefaultCtx)
			if n < 2048 {
				n = 2048
			}
			if n > 1048576 {
				n = 1048576
			}
			state[k] = n
		case "port":
			n := anyInt(v, rmbDefaultPort)
			if n < 1 {
				n = 1
			}
			if n > 65535 {
				n = 65535
			}
			state[k] = n
		case "thinking":
			state[k] = normalizeThinking(v)
		case "use_jinja":
			if s, ok := v.(string); ok {
				word := strings.ToLower(strings.TrimSpace(s))
				if word == "" || word == "auto" {
					state["use_jinja_owner"] = false
				} else {
					state[k] = anyBoolDef(v, true)
					state["use_jinja_owner"] = true
				}
			} else {
				state[k] = anyBoolDef(v, true)
				state["use_jinja_owner"] = true
			}
		case "model_id":
			mid := anyString(v)
			if mapped := catalogIDFromHint(mid); mapped != "" {
				mid = mapped
			}
			state[k] = mid
		default:
			state[k] = v
		}
	}
	state = mergeRmbState(state)
	if err := saveRmbJSON(home, state); err != nil {
		return nil, err
	}
	return state, nil
}

func (s *Server) bindChatToRmb(home string, state map[string]any) {
	path := FindConfigPath(s.homeDir)
	if path == "" {
		path = DefaultConfigPath(s.homeDir)
	}
	cfg := LoadConfig(s.homeDir)
	cfg["llm_provider"] = "rmb"
	base := anyString(state["base_url"])
	if base == "" {
		base = fmt.Sprintf("http://%s:%d/v1", rmbDefaultHost, rmbDefaultPort)
	}
	cfg["llm_base_url"] = base
	model := ""
	if mp := resolveRmbModelPath(state, home); mp != "" {
		model = strings.TrimSuffix(filepath.Base(mp), filepath.Ext(mp))
	}
	if model == "" {
		model = anyString(state["model_id"])
	}
	if model != "" {
		cfg["llm_model"] = model
	}
	cfg["harness_mode"] = "auto"
	cfg["harness_min_context_pct"] = 0.55
	cfg["harness_max_context_pct"] = 0.78
	_ = WriteConfig(path, cfg)
}

func (s *Server) handleRmbSettings(w http.ResponseWriter, r *http.Request) {
	var patch map[string]any
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	if err := dec.Decode(&patch); err != nil && err != io.EOF {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	home := ResolveHomeDir(s.homeDir)
	live := s.claims == nil || !s.claims.AnyActive()
	state, err := s.applyRmbSettingsPatch(home, patch)
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	useAs := anyBoolDef(patch["use_as_chat_provider"], false)
	if useAs {
		s.bindChatToRmb(home, state)
	}
	status := s.rmbStatusPayload()
	if !live {
		status["live"] = false
		status["deferred"] = true
		writeJSON(w, http.StatusConflict, map[string]string{
			"detail": "Saved RMB settings, but cannot restart llama-server while a session is streaming. Stop the current turn first.",
		})
		return
	}
	if s.rmb != nil {
		liveOut, liveErr := s.rmb.ApplyLive(r.Context(), home, state)
		if liveErr == nil && liveOut != nil {
			for k, v := range liveOut {
				status[k] = v
			}
		}
	} else {
		status["live_apply"] = map[string]any{
			"live": false, "restarted": false,
			"live_error": "RMB process controller is not attached",
		}
	}
	status["runtime_applied"] = false
	writeJSON(w, http.StatusOK, status)
}

func (s *Server) handleRmbUse(w http.ResponseWriter, r *http.Request) {
	if s.refuseIfStreaming(w) {
		return
	}
	home := ResolveHomeDir(s.homeDir)
	state, err := s.applyRmbSettingsPatch(home, map[string]any{
		"enabled": true, "auto_start": true,
	})
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]string{"detail": err.Error()})
		return
	}
	s.bindChatToRmb(home, state)
	var start map[string]any
	if s.rmb != nil {
		start, _ = s.rmb.Start(r.Context(), home)
	} else {
		start = map[string]any{
			"ok":    false,
			"error": "RMB process controller is not attached to remedy-runtime.",
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"status":          s.rmbStatusPayload(),
		"start":           start,
		"runtime_applied": false,
	})
}
