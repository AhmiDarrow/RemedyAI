package httpapi

import (
	"net/http"
	"strings"
	"time"
)

// Coordination / nanoswarm / continuity dashboards are Python-worker heavy.
// Go serves Python-shaped idle payloads so Desktop chrome does not 404; live
// swarm mutation stays fail-closed until a worker is wired.

func (s *Server) handleCoordinationPresence(w http.ResponseWriter, r *http.Request) {
	_ = r.URL.Query().Get("session_id")
	writeJSON(w, http.StatusOK, map[string]any{
		"beacons": []any{},
		"count":   0,
		"ts":      float64(time.Now().UnixNano()) / 1e9,
	})
}

func (s *Server) handleNanoswarmStatus(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{
		"name":           "Remedy Nano Swarm",
		"active":         false,
		"event_count":    0,
		"last_event":     nil,
		"last_event_ts":  nil,
		"uptime_s":       0,
		"local_model_id": "",
		"roles":          []string{},
		"bots":           map[string]any{},
		"bundle": map[string]any{
			"model_present": false,
			"model_path":    nil,
			"bundle_root":   nil,
		},
		"catalog": map[string]any{
			"default_model_id": nil,
			"roles":            []string{},
			"bundle_policy":    nil,
		},
		"worker": "not_attached",
		"note":   "nanoswarm workers are not attached to remedy-runtime",
	})
}

func (s *Server) handleNanoswarmTokenStatus(w http.ResponseWriter, _ *http.Request) {
	cfg := LoadConfig(s.homeDir)
	prov := strings.TrimSpace(cfgString(cfg, "llm_provider", ""))
	mod := strings.TrimSpace(cfgString(cfg, "llm_model", ""))
	writeJSON(w, http.StatusOK, map[string]any{
		"bot":              "token",
		"label":            "NanoToken",
		"last_method":      "unavailable",
		"last_estimate":    0,
		"active_provider":  nullIfEmpty(prov),
		"active_model":     nullIfEmpty(mod),
		"bpe_assignment":   map[string]any{},
		"bpe_packs":        []any{},
		"calibrator":       map[string]any{},
		"buckets":          map[string]any{},
		"families":         []any{},
		"worker":           "not_attached",
		"ip_note": "Remedy-owned BBPE packs only; no third-party tokenizer " +
			"libraries or foreign merge tables.",
	})
}

func (s *Server) handleContinuityDashboard(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	if sid == "" {
		sid = "_default"
	}
	cfg := LoadConfig(s.homeDir)
	prov := strings.TrimSpace(cfgString(cfg, "llm_provider", ""))
	mod := strings.TrimSpace(cfgString(cfg, "llm_model", ""))
	harness := strings.TrimSpace(cfgString(cfg, "harness_mode", "auto"))
	if harness == "" {
		harness = "auto"
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"session_id": sid,
		"session_quality": map[string]any{
			"samples": 0,
		},
		"pattern": map[string]any{
			"step_count":   0,
			"success_rate": nil,
			"recent":       []string{},
		},
		"goal": map[string]any{
			"open":                  []string{},
			"stale":                 false,
			"tool_steps_since_sync": 0,
		},
		"scout": map[string]any{
			"last_tools":  []string{},
			"last_active": false,
		},
		"health": map[string]any{
			"error_rate":       0,
			"rate_limit_hits":  0,
			"avg_latency_ms":   nil,
			"flaky":            false,
			"samples":          0,
		},
		"token": map[string]any{
			"last_method":     "unavailable",
			"last_estimate":   0,
			"active_provider": nullIfEmpty(prov),
			"active_model":    nullIfEmpty(mod),
			"last_remeasure":  nil,
			"status": map[string]any{
				"bot":   "token",
				"label": "NanoToken",
				"worker": "not_attached",
			},
		},
		"context_snapshot": nil,
		"harness_mode":     harness,
		"swarm": map[string]any{
			"event_count": 0,
			"last_event":  nil,
		},
	})
}
