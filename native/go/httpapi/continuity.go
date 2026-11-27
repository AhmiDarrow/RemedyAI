package httpapi

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/tokens"
)

// The usage window's Harness tab: what the engine measured about each
// session's context and calls, and the token counter it measured with.

// handleCoordinationPresence lists the conversations that have a turn
// running now, so a window can show what else is being worked on. Nothing
// records which files a turn holds, so none are listed.
func (s *Server) handleCoordinationPresence(w http.ResponseWriter, r *http.Request) {
	me := strings.TrimSpace(r.URL.Query().Get("session_id"))
	beacons := []any{}
	if s.claims != nil && s.sessions != nil {
		text := func(p *string) string {
			if p == nil {
				return ""
			}
			return strings.TrimSpace(*p)
		}
		for _, sid := range s.claims.ActiveSessions() {
			sess, ok, err := s.sessions.Get(sid)
			if err != nil || !ok {
				continue
			}
			muscle := text(sess.Model)
			if prov := text(sess.LLMProvider); prov != "" && muscle != "" {
				muscle = prov + "/" + muscle
			}
			beacons = append(beacons, map[string]any{
				"session_id": sid,
				"you":        sid == me,
				"muscle":     muscle,
				"project":    text(sess.ProjectPath),
				"goal":       sess.Title,
				"phase":      "working",
			})
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"beacons": beacons,
		"count":   len(beacons),
		"ts":      float64(time.Now().UnixNano()) / 1e9,
	})
}

// sessionHarness is what the engine measured over a session's turns. It is
// saved with the session, so the figures are still there after the runtime
// restarts. Nothing here is estimated or carried from elsewhere.
type sessionHarness struct {
	Turns, Compactions, TokensSaved      int
	Window, Budget, Estimate             int
	ModelCalls, ModelErrors, RateLimited int
	Steps, StepsOK                       int
	FailStreak, MaxFailStreak            int
	Recent                               []string
}

const harnessRecentSteps = 12

// harness holds the per-session figures for the running runtime. One runtime
// serves one home, so it is process-wide like the token counter it reports.
var harness = struct {
	sync.Mutex
	bySession map[string]*sessionHarness
	latest    string
	// home is where the figures are saved (the runtime's home). Empty until
	// the first turn or the first read names it; then nothing is saved.
	home string
}{bySession: map[string]*sessionHarness{}}

// setHarnessHome names where the figures are saved, so a runtime that has just
// started can show what earlier turns measured.
func setHarnessHome(homeDir string) {
	harness.Lock()
	harness.home = ResolveHomeDir(strings.TrimSpace(homeDir))
	harness.Unlock()
}

// harnessPath is a session's saved figures; "" when there is nowhere to save.
// The caller holds the lock.
func harnessPath(sid string) string {
	if harness.home == "" || sid == "" || strings.ContainsAny(sid, `/\.`) {
		return ""
	}
	return filepath.Join(harness.home, "sessions", sid, "harness.json")
}

// loadHarnessLocked reads a session's saved figures into memory, and for an
// unnamed session the one that ran last. The caller holds the lock.
func loadHarnessLocked(sid string) *sessionHarness {
	if sid == "" && harness.home != "" {
		if b, err := os.ReadFile(filepath.Join(harness.home, "sessions", "harness.latest")); err == nil {
			sid = strings.TrimSpace(string(b))
			harness.latest = sid
		}
	}
	if h := harness.bySession[sid]; h != nil {
		return h
	}
	path := harnessPath(sid)
	if path == "" {
		return nil
	}
	b, err := os.ReadFile(path)
	if err != nil {
		return nil
	}
	h := &sessionHarness{}
	if json.Unmarshal(b, h) != nil {
		return nil
	}
	harness.bySession[sid] = h
	return h
}

// saveHarnessLocked writes a session's figures next to its other files. A
// failed write loses nothing but the figures' survival across a restart.
func saveHarnessLocked(sid string, h *sessionHarness) {
	path := harnessPath(sid)
	if path == "" {
		return
	}
	b, err := json.Marshal(h)
	if err != nil || os.MkdirAll(filepath.Dir(path), 0o700) != nil {
		return
	}
	_ = os.WriteFile(path, b, 0o600)
	_ = os.WriteFile(filepath.Join(harness.home, "sessions", "harness.latest"), []byte(sid), 0o600)
}

// recordHarness adds one finished turn to its session's figures.
func recordHarness(homeDir, sessionID string, out *cognition.Outcome) {
	if out == nil {
		return
	}
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		sid = "_default"
	}
	c := out.Context
	harness.Lock()
	defer harness.Unlock()
	if home := strings.TrimSpace(homeDir); home != "" {
		harness.home = ResolveHomeDir(home)
	}
	h := loadHarnessLocked(sid)
	if h == nil {
		h = &sessionHarness{}
		harness.bySession[sid] = h
	}
	harness.latest = sid
	defer saveHarnessLocked(sid, h)
	h.Turns++
	h.Compactions += c.Compactions
	h.TokensSaved += c.TokensSaved
	h.Window, h.Budget, h.Estimate = c.Window, c.Budget, c.Estimate
	h.ModelCalls += c.ModelCalls
	h.ModelErrors += c.ModelErrors
	h.RateLimited += c.RateLimited
	for _, step := range c.Steps {
		h.Steps++
		label := step.Name
		if step.OK {
			h.StepsOK++
			h.FailStreak = 0
		} else {
			label += " (failed)"
			h.FailStreak++
			if h.FailStreak > h.MaxFailStreak {
				h.MaxFailStreak = h.FailStreak
			}
		}
		h.Recent = append(h.Recent, label)
	}
	if n := len(h.Recent); n > harnessRecentSteps {
		h.Recent = append([]string(nil), h.Recent[n-harnessRecentSteps:]...)
	}
}

// harnessFor returns a copy of a session's figures; an unnamed session reads
// the one that ran last. The zero value means no turn has run.
func harnessFor(sessionID string) sessionHarness {
	harness.Lock()
	defer harness.Unlock()
	sid := strings.TrimSpace(sessionID)
	if sid == "" || sid == "_default" {
		if _, ok := harness.bySession[sid]; !ok {
			sid = harness.latest
		}
	}
	h := harness.bySession[sid]
	if h == nil {
		// Not counted since this runtime started: read what was saved.
		h = loadHarnessLocked(sid)
	}
	if h == nil {
		return sessionHarness{}
	}
	out := *h
	out.Recent = append([]string{}, h.Recent...)
	return out
}

// harnessShares reads the Memory harness setting: the shares of the model's
// window at which old tool output is trimmed and the transcript compacted.
// Off keeps the transcript whole until it would no longer fit the window.
func harnessShares(cfg ConfigMap) (trimAt, compactAt float64) {
	if strings.ToLower(strings.TrimSpace(cfgString(cfg, "harness_mode", "auto"))) != "auto" {
		return 1, 1
	}
	share := func(key string, def float64) float64 {
		v := anyFloat(cfg[key], def)
		if v <= 0 || v > 1 {
			return def
		}
		return v
	}
	trimAt = share("harness_min_context_pct", 0.75)
	compactAt = share("harness_max_context_pct", 0.92)
	if trimAt > compactAt {
		trimAt = compactAt
	}
	return trimAt, compactAt
}

const tokenNote = "Counted with the " + tokens.Method + " encoding " +
	"(github.com/tiktoken-go/tokenizer, MIT). Exact for models that use it, " +
	"an estimate for the others; what a provider bills is what its API reports."

func (s *Server) handleNanoswarmTokenStatus(w http.ResponseWriter, _ *http.Request) {
	cfg := LoadConfig(s.homeDir)
	h := harnessFor("")
	writeJSON(w, http.StatusOK, map[string]any{
		"label":           "Tokenizer",
		"last_method":     tokens.Method,
		"last_estimate":   h.Estimate,
		"active_provider": nullIfEmpty(strings.TrimSpace(cfgString(cfg, "llm_provider", ""))),
		"active_model":    nullIfEmpty(strings.TrimSpace(cfgString(cfg, "llm_model", ""))),
		"ip_note":         tokenNote,
	})
}

func (s *Server) handleContinuityDashboard(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	h := harnessFor(sid)
	if sid == "" {
		sid = "_default"
	}
	cfg := LoadConfig(s.homeDir)
	token := map[string]any{
		"last_method":     tokens.Method,
		"last_estimate":   h.Estimate,
		"active_provider": nullIfEmpty(strings.TrimSpace(cfgString(cfg, "llm_provider", ""))),
		"active_model":    nullIfEmpty(strings.TrimSpace(cfgString(cfg, "llm_model", ""))),
	}
	if h.Window > 0 {
		token["last_remeasure"] = map[string]any{
			"fill_pct": float64(h.Estimate) / float64(h.Window),
			"window":   h.Window,
			"budget":   h.Budget,
		}
	}
	pattern := map[string]any{"step_count": h.Steps, "recent": h.Recent}
	if h.Steps > 0 {
		pattern["success_rate"] = float64(h.StepsOK) / float64(h.Steps)
	}
	health := map[string]any{"samples": h.ModelCalls, "rate_limit_hits": h.RateLimited}
	if h.ModelCalls > 0 {
		health["error_rate"] = float64(h.ModelErrors) / float64(h.ModelCalls)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"session_id": sid,
		"session_quality": map[string]any{
			"turns":                    h.Turns,
			"compress_count":           h.Compactions,
			"tokens_saved_by_compress": h.TokensSaved,
			"max_tool_fail_streak":     h.MaxFailStreak,
		},
		"pattern": pattern,
		"health":  health,
		"token":   token,
	})
}
