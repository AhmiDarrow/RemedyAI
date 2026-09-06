package httpapi

import (
	"net/http"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"
)

func (s *Server) handleDiagnostics(w http.ResponseWriter, r *http.Request) {
	t0 := time.Now()
	probe := false
	switch strings.ToLower(strings.TrimSpace(r.URL.Query().Get("probe_providers"))) {
	case "1", "true", "yes", "on":
		probe = true
	}
	body := s.collectDiagnostics(probe)
	elapsed := float64(time.Since(t0).Microseconds()) / 1000.0
	body["collect_ms"] = round1(elapsed)
	writeJSON(w, http.StatusOK, body)
}

func round1(v float64) float64 {
	return float64(int(v*10+0.5)) / 10
}

func (s *Server) collectDiagnostics(probeProviders bool) map[string]any {
	cfg := LoadConfig(s.homeDir)
	home := ResolveHomeDir(s.homeDir)
	uptimeSec := time.Since(s.started).Seconds()
	if uptimeSec < 0 {
		uptimeSec = 0
	}

	memEntries, summaries, chats := 0, 0, 0
	if s.sessions != nil {
		if m, sum, c, err := s.sessions.StatusCounts(); err == nil {
			memEntries, summaries, chats = m, sum, c
		}
	}

	rmb := s.rmbStatusPayload()
	vision := s.visionStatusPayload(false)

	provPayload := s.listConnectedProviders()
	connected, _ := provPayload["connected"].([]map[string]any)
	if connected == nil {
		connected = []map[string]any{}
	}
	providerRows := make([]map[string]any, 0, len(connected))
	remoteN, localN := 0, 0
	for _, p := range connected {
		pid := anyString(p["id"])
		local := pid == "ollama" || pid == "rmb" || pid == "local"
		if local {
			localN++
		} else {
			remoteN++
		}
		providerRows = append(providerRows, map[string]any{
			"id":         pid,
			"label":      p["label"],
			"connected":  true,
			"enabled":    p["enabled"],
			"reason":     p["connect_reason"],
			"local":      local,
			"last_model": p["last_model"],
			"base_url":   p["base_url"],
			"badge":      p["badge"],
		})
	}

	activeProv := strings.TrimSpace(cfgString(cfg, "llm_provider", ""))
	activeModel := strings.TrimSpace(cfgString(cfg, "llm_model", ""))

	issues := make([]map[string]any, 0)
	if activeProv == "" || activeModel == "" {
		issues = append(issues, map[string]any{
			"severity": "info",
			"area":     "providers",
			"message":  "No active provider/model configured",
			"hint":     "Open Settings → Provider to connect a model",
		})
	}
	if !anyBoolDef(rmb["ready"], false) && anyBoolDef(rmb["enabled"], false) {
		issues = append(issues, map[string]any{
			"severity": "warn",
			"area":     "rmb",
			"message":  "RMB enabled but not ready",
			"hint":     anyString(rmb["not_ready_hint"]),
		})
	}

	overall := "ok"
	for _, iss := range issues {
		sev := anyString(iss["severity"])
		if sev == "error" {
			overall = "error"
			break
		}
		if sev == "warn" && overall == "ok" {
			overall = "degraded"
		}
	}

	b := s.bridge()
	b.mu.Lock()
	hostConnected := b.hostConnectedLocked()
	pendingJobs := b.pendingCount()
	jobsRoot := b.root
	b.mu.Unlock()

	disk := homeDiskStats(home)

	probes := []any{}
	if probeProviders {
		for _, p := range providerRows {
			if !anyBoolDef(p["local"], false) {
				continue
			}
			base := anyString(p["base_url"])
			if base == "" {
				continue
			}
			probes = append(probes, map[string]any{
				"id":         p["id"],
				"base_url":   base,
				"latency_ms": nil,
				"ok":         false,
			})
		}
	}

	return map[string]any{
		"ok":         overall != "error",
		"overall":    overall,
		"checked_at": time.Now().UTC().Format("2006-01-02T15:04:05Z"),
		"issues":     issues,
		"remedy": map[string]any{
			"version":             s.version,
			"uptime":              formatUptime(time.Since(s.started)),
			"uptime_seconds":      round1(uptimeSec),
			"api": map[string]any{
				"host":      "127.0.0.1",
				"port":      s.apiListenPort,
				"listening": true,
			},
			"process": map[string]any{
				"pid":     os.Getpid(),
				"threads": runtime.NumGoroutine(),
			},
			"memory_entries":      memEntries,
			"skills_count":        s.skillsCount(),
			"sessions_count":      summaries,
			"chat_sessions_count": chats,
			"home_dir":            home,
			"home_disk":           disk,
			"log_dir":             filepath.Join(home, "logs"),
			"active_provider":     nullIfEmpty(activeProv),
			"active_model":        nullIfEmpty(activeModel),
			"health_checks":       map[string]any{},
			"runtime":             "go",
		},
		"rmb": map[string]any{
			"ok":               rmb["ok"],
			"enabled":          rmb["enabled"],
			"running":          rmb["running"],
			"ready":            rmb["ready"],
			"starting":         rmb["starting"],
			"model_id":         rmb["model_id"],
			"model_name":       anyString(asStringMapOrEmpty(rmb["model"])["name"]),
			"model_path":       rmb["model_path"],
			"model_present":    rmb["model_present"],
			"runtime_present":  rmb["runtime_present"],
			"ctx_size":         rmb["ctx_size"],
			"n_gpu_layers":     rmb["n_gpu_layers"],
			"profile":          rmb["profile"],
			"host":             rmb["host"],
			"port":             rmb["port"],
			"base_url":         rmb["base_url"],
			"not_ready_hint":   rmb["not_ready_hint"],
			"vision_suspended": rmb["vision_suspended"],
			"local_agent_mode": rmb["local_agent_mode"],
		},
		"vision": map[string]any{
			"ok":                 anyBoolDef(vision["ready"], false) || anyBoolDef(vision["installed"], false),
			"enabled":            vision["enabled"],
			"running":            vision["running"],
			"ready":              vision["ready"],
			"model_id":           vision["model_id"],
			"port":               vision["port"],
			"base_url":           vision["base_url"],
			"suspended_for_rmb":  anyBoolDef(rmb["vision_suspended"], false),
			"not_ready_hint":     vision["not_ready_hint"],
		},
		"hardware": map[string]any{
			"platform": runtime.GOOS,
			"system":   runtime.GOOS,
			"machine":  runtime.GOARCH,
			"python":   nil,
			"hostname": hostnameOrEmpty(),
			"cpu": map[string]any{
				"count_logical": runtime.NumCPU(),
				"percent":       nil,
				"brand":         nil,
			},
			"memory": map[string]any{
				"total_gb":     nil,
				"available_gb": nil,
				"used_pct":     nil,
			},
			"gpu": map[string]any{
				"nvidia": false,
				"gpus":   []any{},
			},
			"disks": []any{disk},
		},
		"providers": map[string]any{
			"active": map[string]any{
				"provider": nullIfEmpty(activeProv),
				"model":    nullIfEmpty(activeModel),
				"base_url": nullIfEmpty(cfgString(cfg, "llm_base_url", "")),
			},
			"connected_count":        len(providerRows),
			"remote_connected_count": remoteN,
			"local_connected_count":  localN,
			"providers":              providerRows,
			"usage_7d":               []any{},
			"probes":                 probes,
			"ollama":                 detectOllama("", 800*time.Millisecond),
		},
		"computer": map[string]any{
			"ok":             hostConnected == true,
			"host_connected": hostConnected,
			"pending_jobs":   pendingJobs,
			"jobs_root":      jobsRoot,
		},
	}
}

func asStringMapOrEmpty(v any) map[string]any {
	if m, ok := asStringMap(v); ok && m != nil {
		return m
	}
	return map[string]any{}
}

func hostnameOrEmpty() string {
	h, err := os.Hostname()
	if err != nil {
		return ""
	}
	return h
}

func homeDiskStats(home string) map[string]any {
	out := map[string]any{"path": home}
	st, err := os.Stat(home)
	if err != nil || !st.IsDir() {
		out["error"] = "home missing"
		return out
	}
	// Cross-platform free-space probing is OS-specific; keep path only when unavailable.
	return out
}
