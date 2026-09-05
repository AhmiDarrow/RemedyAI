package httpapi

import (
	"bufio"
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"strconv"
	"strings"
	"sync/atomic"
	"time"
)

const selfInjectLedgerName = "self_inject_ledger.jsonl"
const selfImproveLastName = "self_improve_last.json"
const selfImprovePendingShipName = "self_improve_pending_ship.json"

var remedyAINameRE = regexp.MustCompile(`(?m)^name\s*=\s*["']remedy-ai["']`)

func selfInjectLedgerPath(homeDir string) string {
	return filepath.Join(ResolveHomeDir(homeDir), selfInjectLedgerName)
}

func readSelfInjectLedger(homeDir string) []map[string]any {
	path := selfInjectLedgerPath(homeDir)
	f, err := os.Open(path)
	if err != nil {
		return nil
	}
	defer f.Close()
	out := make([]map[string]any, 0)
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var row map[string]any
		if json.Unmarshal([]byte(line), &row) != nil || row == nil {
			continue
		}
		out = append(out, row)
	}
	return out
}

func (s *Server) serveStartedUTC() string {
	t := s.started
	if t.IsZero() {
		t = time.Now()
	}
	return t.UTC().Format("2006-01-02T15:04:05-07:00")
}

func (s *Server) selfInjectLiveState(r map[string]any) string {
	if anyString(r["status"]) != "applied" {
		return ""
	}
	if anyString(r["tree"]) == "desktop" {
		return "live"
	}
	finished := anyString(r["finished_utc"])
	detail, _ := r["detail"].(map[string]any)
	requested := true
	if detail != nil {
		if v, ok := detail["sidecar_restart_requested"]; ok {
			requested = anyBoolDef(v, true)
		}
	}
	started := s.serveStartedUTC()
	if finished != "" && finished <= started {
		return "live"
	}
	if requested {
		return "awaiting_restart"
	}
	return "not_loaded"
}

func (s *Server) handleSelfInjectRounds(w http.ResponseWriter, r *http.Request) {
	limit := 20
	if raw := strings.TrimSpace(r.URL.Query().Get("limit")); raw != "" {
		if n, err := strconv.Atoi(raw); err == nil {
			limit = n
		}
	}
	if limit < 1 {
		limit = 1
	}
	if limit > 100 {
		limit = 100
	}
	all := readSelfInjectLedger(s.homeDir)
	// newest first, take last N then reverse
	if len(all) > limit {
		all = all[len(all)-limit:]
	}
	rounds := make([]map[string]any, 0, len(all))
	for i := len(all) - 1; i >= 0; i-- {
		row := all[i]
		clone := cloneMap(row)
		delete(clone, "diff")
		clone["live_state"] = s.selfInjectLiveState(row)
		rounds = append(rounds, clone)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"serve_started_utc": s.serveStartedUTC(),
		"rounds":            rounds,
	})
}

func (s *Server) NoteUserActivity() {
	atomic.StoreInt64(&s.lastUserActivityUnixNano, time.Now().UnixNano())
}

func (s *Server) lastUserActivityUnix() float64 {
	n := atomic.LoadInt64(&s.lastUserActivityUnixNano)
	if n <= 0 {
		return 0
	}
	return float64(n) / 1e9
}

func (s *Server) processStartedUnix() float64 {
	t := s.started
	if t.IsZero() {
		t = time.Now()
	}
	return float64(t.UnixNano()) / 1e9
}

func (s *Server) idleSeconds() float64 {
	mark := s.lastUserActivityUnix()
	if mark <= 0 {
		mark = s.processStartedUnix()
	}
	idle := float64(time.Now().UnixNano())/1e9 - mark
	if idle < 0 {
		return 0
	}
	return idle
}

func selfInjectIdleThreshold(homeDir string) float64 {
	cfg := LoadConfig(homeDir)
	section, _ := asStringMap(cfg["self_inject"])
	if section == nil {
		return 300
	}
	v := cfgFloat(ConfigMap(section), "idle_seconds", 300)
	if v < 10 {
		return 10
	}
	return v
}

func isDesktopPackagedRuntime() bool {
	for _, key := range []string{"REMEDY_DESKTOP", "REMEDY_DESKTOP_SIDECAR"} {
		switch strings.ToLower(strings.TrimSpace(os.Getenv(key))) {
		case "1", "true", "yes", "on":
			return true
		}
	}
	return false
}

func selfInjectEnabled(homeDir string) bool {
	switch strings.TrimSpace(os.Getenv("REMEDY_SELF_INJECT")) {
	case "0":
		return false
	case "1":
		return true
	}
	packaged := isDesktopPackagedRuntime()
	cfg := LoadConfig(homeDir)
	section, _ := asStringMap(cfg["self_inject"])
	if section != nil {
		if _, ok := section["enabled"]; ok {
			return cfgBool(ConfigMap(section), "enabled", !packaged)
		}
	}
	return !packaged
}

func selfInjectShouldRunNow(homeDir string, idleS, threshold float64) bool {
	if strings.TrimSpace(os.Getenv("REMEDY_SELF_INJECT_FORCE")) == "1" {
		return true
	}
	if !selfInjectEnabled(homeDir) {
		return false
	}
	return idleS >= threshold
}

func isSourceCheckout(repo string) bool {
	repo = strings.TrimSpace(repo)
	if repo == "" {
		return false
	}
	if _, err := os.Stat(filepath.Join(repo, ".git")); err != nil {
		return false
	}
	raw, err := os.ReadFile(filepath.Join(repo, "pyproject.toml"))
	if err != nil {
		return false
	}
	return remedyAINameRE.Match(raw)
}

func guessRemedyRepo() string {
	if dev := strings.TrimSpace(os.Getenv("REMEDY_DEV_ROOT")); dev != "" {
		if isSourceCheckout(dev) {
			return dev
		}
	}
	cwd, err := os.Getwd()
	if err != nil || cwd == "" {
		return ""
	}
	here := cwd
	for i := 0; i < 8; i++ {
		if isSourceCheckout(here) {
			return here
		}
		parent := filepath.Dir(here)
		if parent == here {
			break
		}
		here = parent
	}
	return ""
}

func clientUpdatePolicy(repo string) map[string]any {
	source := isSourceCheckout(repo)
	mode := "replace"
	if source {
		mode = "source_ship"
	}
	return map[string]any{
		"mode":              mode,
		"self_improve_code": source,
		"on_conflict":       "origin_wins",
		"ship_from_idle":    false,
		"note": "Source checkout may draft locally; ship via git_push/gh_release. " +
			"Packaged clients never rewrite their install — the signed " +
			"release replaces them. Local self-improve is never merged into " +
			"an official update.",
	}
}

func readSelfImproveLastTick(homeDir string) any {
	path := filepath.Join(ResolveHomeDir(homeDir), selfImproveLastName)
	var data map[string]any
	if err := readJSONFile(path, &data); err != nil || data == nil {
		return nil
	}
	return data
}

func readPendingShip(homeDir string) any {
	path := filepath.Join(ResolveHomeDir(homeDir), selfImprovePendingShipName)
	var data map[string]any
	if err := readJSONFile(path, &data); err != nil || data == nil {
		return nil
	}
	return data
}

func (s *Server) activitySnapshot() map[string]any {
	idleS := s.idleSeconds()
	threshold := selfInjectIdleThreshold(s.homeDir)
	repo := guessRemedyRepo()
	return map[string]any{
		"enabled":            selfInjectEnabled(s.homeDir),
		"idle_ready":         selfInjectShouldRunNow(s.homeDir, idleS, threshold),
		"idle_s":             float64(int(idleS*10+0.5)) / 10,
		"idle_threshold_s":   threshold,
		"last_user_activity": s.lastUserActivityUnix(),
		"process_started":    s.processStartedUnix(),
		"last_tick":          readSelfImproveLastTick(s.homeDir),
		"update":             clientUpdatePolicy(repo),
		"pending_ship":       readPendingShip(s.homeDir),
	}
}

func (s *Server) handleSelfImprove(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, s.activitySnapshot())
}
