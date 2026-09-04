package httpapi

import (
	"net/http"
	"strings"
)

func (s *Server) handlePartnerStatus(w http.ResponseWriter, r *http.Request) {
	cfg := LoadConfig(s.homeDir)
	_ = s.approvals.SyncFromConfig(cfg)

	sidQ := strings.TrimSpace(r.URL.Query().Get("session_id"))
	pending := s.approvals.ListPending("")
	openGoals, activeTitle, nextAction, lastStep := s.openGoalsSummary()

	accessScope := cfgString(cfg, "access_scope", "project")
	harness := cfgString(cfg, "harness_mode", "auto")

	sidKey := sidQ
	if sidKey == "" {
		sidKey = s.FocusedSessionID()
	}

	approvalsOut := make([]map[string]any, 0, 5)
	for i, it := range pending {
		if i >= 5 {
			break
		}
		approvalsOut = append(approvalsOut, s.approvals.ToPublic(it))
	}

	var sessionID any
	if sidKey != "" {
		sessionID = sidKey
	}
	var active any
	if activeTitle != "" {
		active = activeTitle
	}
	var next any
	if nextAction != "" {
		next = nextAction
	}
	lifeFolder := s.lifeFolderPath()
	var lifeFolderOut any
	if lifeFolder != "" {
		lifeFolderOut = lifeFolder
	}

	organism := map[string]any{
		"alive":      openGoals > 0,
		"open_count": openGoals,
		"life_title": activeTitle,
		"next_action": nextAction,
	}
	if lastStep != nil {
		if m, ok := lastStep.(map[string]any); ok {
			organism["last_did"] = asString(m["did"])
		}
	}
	organism["life_folder"] = lifeFolder

	writeJSON(w, http.StatusOK, map[string]any{
		"version":           s.version,
		"pending_approvals": len(pending),
		"approval_mode":     s.approvals.Mode(),
		"open_goals":        openGoals,
		"active_goal":       active,
		"next_action":       next,
		"last_step":         lastStep,
		"life_folder":       lifeFolderOut,
		"cas":               nil,
		"organism":          organism,
		"access_scope":      accessScope,
		"harness_mode":      harness,
		"brief_intent":      "",
		"session_id":        sessionID,
		"approvals":         approvalsOut,
		"nanoswarm":         map[string]any{"active": false},
		"session_quality":   map[string]any{},
		"provider_health":   map[string]any{},
		"metabolism":        map[string]any{},
		"soma":              map[string]any{},
	})
}

func (s *Server) openGoalsSummary() (openCount int, activeTitle, nextAction string, lastStep any) {
	lifeGoalMu.Lock()
	f := s.loadLifeGoalsFile()
	lifeGoalMu.Unlock()

	var firstOpen *lifeGoal
	var active *lifeGoal
	for i := range f.Goals {
		g := &f.Goals[i]
		if g.Status != "open" && g.Status != "active" {
			continue
		}
		openCount++
		if firstOpen == nil {
			cp := *g
			firstOpen = &cp
		}
		if g.Status == "active" && active == nil {
			cp := *g
			active = &cp
		}
	}
	if active == nil {
		active = firstOpen
	}
	if active != nil {
		activeTitle = active.Title
		nextAction = active.NextAction
	}
	if len(f.LastSteps) > 0 {
		lastStep = f.LastSteps[len(f.LastSteps)-1]
	}
	return
}
