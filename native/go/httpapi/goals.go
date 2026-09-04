package httpapi

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"strings"
	"sync"
)

var lifeGoalMu sync.Mutex

type lifeGoal struct {
	ID            string   `json:"id"`
	Title         string   `json:"title"`
	Why           string   `json:"why"`
	Horizon       string   `json:"horizon"`
	DoneLooksLike string   `json:"done_looks_like"`
	NextAction    string   `json:"next_action"`
	NextBy        string   `json:"next_by"`
	Status        string   `json:"status"`
	Evidence      []string `json:"evidence"`
	Source        string   `json:"source"`
	CreatedAt     string   `json:"created_at"`
	UpdatedAt     string   `json:"updated_at"`
}

type lifeGoalsFile struct {
	Version      int              `json:"version"`
	UpdatedAt    string           `json:"updated_at"`
	LastPulseAt  float64          `json:"last_pulse_at"`
	LastDriveAt  float64          `json:"last_drive_at"`
	LastDigestAt float64          `json:"last_digest_at"`
	LastSteps    []map[string]any `json:"last_steps"`
	Goals        []lifeGoal       `json:"goals"`
}

func (s *Server) lifeGoalsPath() string {
	return filepath.Join(s.remedyHomeDir(), "life_goals.json")
}

func (s *Server) visibleLifeDir() string {
	home := s.remedyHomeDir()
	d := filepath.Join(home, "life")
	_ = os.MkdirAll(d, 0o700)
	return d
}

func emptyLifeGoals() lifeGoalsFile {
	return lifeGoalsFile{
		Version:   1,
		LastSteps: []map[string]any{},
		Goals:     []lifeGoal{},
	}
}

func (s *Server) loadLifeGoalsFile() lifeGoalsFile {
	path := s.lifeGoalsPath()
	var raw map[string]any
	if err := readJSONFile(path, &raw); err != nil {
		return emptyLifeGoals()
	}
	out := emptyLifeGoals()
	out.Version = 1
	if v, ok := raw["last_pulse_at"].(float64); ok {
		out.LastPulseAt = v
	}
	if v, ok := raw["last_drive_at"].(float64); ok {
		out.LastDriveAt = v
	}
	if v, ok := raw["last_digest_at"].(float64); ok {
		out.LastDigestAt = v
	}
	if steps, ok := raw["last_steps"].([]any); ok {
		for _, row := range steps {
			m, ok := row.(map[string]any)
			if !ok {
				continue
			}
			did := asString(m["did"])
			goal := asString(m["goal"])
			if did == "" && goal == "" {
				continue
			}
			ts, _ := m["ts"].(float64)
			out.LastSteps = append(out.LastSteps, map[string]any{
				"ts":   ts,
				"goal": truncateRunes(goal, 200),
				"did":  truncateRunes(did, 280),
				"next": truncateRunes(asString(m["next"]), 280),
				"path": truncateRunes(asString(m["path"]), 400),
				"kind": truncateRunes(asString(m["kind"]), 32),
			})
		}
		if len(out.LastSteps) > 12 {
			out.LastSteps = out.LastSteps[len(out.LastSteps)-12:]
		}
	}
	rows, _ := raw["goals"].([]any)
	for _, row := range rows {
		m, ok := row.(map[string]any)
		if !ok {
			continue
		}
		title := strings.TrimSpace(asString(m["title"]))
		if title == "" {
			continue
		}
		hz := strings.ToLower(asString(m["horizon"]))
		if hz != "week" && hz != "season" && hz != "life" {
			hz = "season"
		}
		st := strings.ToLower(asString(m["status"]))
		switch st {
		case "open", "active", "paused", "done", "dropped":
		default:
			st = "open"
		}
		id := asString(m["id"])
		if id == "" {
			id = shortHexID(12)
		}
		src := asString(m["source"])
		if src == "" {
			src = "chat"
		}
		created := asString(m["created_at"])
		if created == "" {
			created = utcNowISO()
		}
		updated := asString(m["updated_at"])
		if updated == "" {
			updated = created
		}
		why := asString(m["why"])
		if why == "" {
			why = asString(m["description"])
		}
		out.Goals = append(out.Goals, lifeGoal{
			ID:            id,
			Title:         truncateRunes(title, 200),
			Why:           truncateRunes(why, 400),
			Horizon:       hz,
			DoneLooksLike: truncateRunes(asString(m["done_looks_like"]), 280),
			NextAction:    truncateRunes(asString(m["next_action"]), 280),
			NextBy:        truncateRunes(asString(m["next_by"]), 40),
			Status:        st,
			Evidence:      stringList(m["evidence"]),
			Source:        truncateRunes(src, 32),
			CreatedAt:     created,
			UpdatedAt:     updated,
		})
		if len(out.Goals) >= 80 {
			break
		}
	}
	return out
}

func (s *Server) saveLifeGoalsFile(f lifeGoalsFile) error {
	f.Version = 1
	f.UpdatedAt = utcNowISO()
	if f.LastSteps == nil {
		f.LastSteps = []map[string]any{}
	}
	if f.Goals == nil {
		f.Goals = []lifeGoal{}
	}
	home := s.remedyHomeDir()
	if err := os.MkdirAll(home, 0o700); err != nil {
		return err
	}
	return writeJSONAtomic(s.lifeGoalsPath(), f)
}

func (g lifeGoal) toPublic() map[string]any {
	ev := g.Evidence
	if ev == nil {
		ev = []string{}
	}
	return map[string]any{
		"id":              g.ID,
		"title":           g.Title,
		"why":             g.Why,
		"horizon":         g.Horizon,
		"done_looks_like": g.DoneLooksLike,
		"next_action":     g.NextAction,
		"next_by":         g.NextBy,
		"status":          g.Status,
		"evidence":        ev,
		"source":          g.Source,
		"created_at":      g.CreatedAt,
		"updated_at":      g.UpdatedAt,
	}
}

func inventNextAction(title string) string {
	t := strings.TrimSpace(title)
	if t == "" {
		t = "this"
	}
	low := strings.ToLower(t)
	switch {
	case strings.Contains(low, "novel") || strings.Contains(low, "book") || strings.Contains(low, "write"):
		return "Draft a one-page outline for " + t
	case strings.Contains(low, "job") || strings.Contains(low, "resume") || strings.Contains(low, "career"):
		return "Draft resume bullets toward " + t
	case strings.Contains(low, "learn") || strings.Contains(low, "study") || strings.Contains(low, "course"):
		return "Write a 20-minute practice plan for " + t
	case strings.Contains(low, "ship") || strings.Contains(low, "launch") || strings.Contains(low, "app"):
		return "Write this week's three ship moves for " + t
	default:
		return "Write the next 15-minute move for " + t
	}
}

func (s *Server) addLifeGoal(title, why, horizon, nextAction, doneLooksLike, source string) (lifeGoal, error) {
	lifeGoalMu.Lock()
	defer lifeGoalMu.Unlock()
	f := s.loadLifeGoalsFile()
	t := strings.TrimSpace(title)
	if t == "" {
		return lifeGoal{}, errTitleRequired
	}
	why = strings.TrimSpace(why)
	nextAction = strings.TrimSpace(nextAction)
	doneLooksLike = strings.TrimSpace(doneLooksLike)
	hz := strings.ToLower(strings.TrimSpace(horizon))
	if hz != "week" && hz != "season" && hz != "life" {
		hz = "season"
	}
	if source == "" {
		source = "api"
	}
	// Refresh existing open match by title.
	needle := strings.ToLower(t)
	for i := range f.Goals {
		g := &f.Goals[i]
		if (g.Status == "open" || g.Status == "active") &&
			(strings.Contains(strings.ToLower(g.Title), needle) || strings.EqualFold(g.Title, t)) {
			if why != "" && g.Why == "" {
				g.Why = truncateRunes(why, 400)
			}
			if nextAction != "" {
				g.NextAction = truncateRunes(nextAction, 280)
			}
			if doneLooksLike != "" && g.DoneLooksLike == "" {
				g.DoneLooksLike = truncateRunes(doneLooksLike, 280)
			}
			g.UpdatedAt = utcNowISO()
			_ = s.saveLifeGoalsFile(f)
			return *g, nil
		}
	}
	status := "open"
	openN := 0
	for _, g := range f.Goals {
		if g.Status == "open" || g.Status == "active" {
			openN++
		}
	}
	if openN == 0 {
		status = "active"
	}
	g := lifeGoal{
		ID:            shortHexID(12),
		Title:         truncateRunes(t, 200),
		Why:           truncateRunes(why, 400),
		Horizon:       hz,
		DoneLooksLike: truncateRunes(doneLooksLike, 280),
		NextAction:    truncateRunes(nextAction, 280),
		Status:        status,
		Evidence:      []string{},
		Source:        truncateRunes(source, 32),
		CreatedAt:     utcNowISO(),
		UpdatedAt:     utcNowISO(),
	}
	if g.NextAction == "" {
		g.NextAction = inventNextAction(g.Title)
	}
	f.Goals = append([]lifeGoal{g}, f.Goals...)
	if len(f.Goals) > 80 {
		f.Goals = f.Goals[:80]
	}
	_ = s.saveLifeGoalsFile(f)
	return g, nil
}

type goalError string

func (e goalError) Error() string { return string(e) }

const errTitleRequired goalError = "title required"

func (s *Server) patchLifeGoal(id string, fields map[string]any) (*lifeGoal, bool) {
	lifeGoalMu.Lock()
	defer lifeGoalMu.Unlock()
	f := s.loadLifeGoalsFile()
	needle := strings.ToLower(strings.TrimSpace(id))
	idx := -1
	for i, g := range f.Goals {
		if strings.EqualFold(g.ID, needle) || strings.EqualFold(g.Title, needle) {
			idx = i
			break
		}
	}
	if idx < 0 {
		return nil, false
	}
	g := f.Goals[idx]
	if st, ok := fields["status"].(string); ok && st == "done" {
		ev := strings.TrimSpace(asString(fields["evidence"]))
		if ev != "" {
			g.Evidence = append(g.Evidence, truncateRunes(ev, 240))
			if len(g.Evidence) > 24 {
				g.Evidence = g.Evidence[len(g.Evidence)-24:]
			}
		}
		g.Status = "done"
	} else if st, ok := fields["status"].(string); ok && st == "paused" {
		g.Status = "paused"
	} else {
		if v, ok := fields["title"]; ok {
			if t := strings.TrimSpace(asString(v)); t != "" {
				g.Title = truncateRunes(t, 200)
			}
		}
		if v, ok := fields["status"]; ok {
			st := strings.ToLower(asString(v))
			switch st {
			case "open", "active", "paused", "done", "dropped":
				g.Status = st
			}
		}
		if v, ok := fields["next_action"]; ok && v != nil {
			g.NextAction = truncateRunes(asString(v), 280)
			if g.NextAction != "" && g.Status == "open" {
				g.Status = "active"
			}
		}
		if v, ok := fields["next_by"]; ok && v != nil {
			g.NextBy = truncateRunes(asString(v), 40)
		}
		if v, ok := fields["done_looks_like"]; ok && v != nil {
			g.DoneLooksLike = truncateRunes(asString(v), 280)
		}
		if v, ok := fields["why"]; ok && v != nil {
			g.Why = truncateRunes(asString(v), 400)
		}
		if ev := strings.TrimSpace(asString(fields["evidence"])); ev != "" {
			g.Evidence = append(g.Evidence, truncateRunes(ev, 240))
			if len(g.Evidence) > 24 {
				g.Evidence = g.Evidence[len(g.Evidence)-24:]
			}
		}
	}
	g.UpdatedAt = utcNowISO()
	f.Goals[idx] = g
	_ = s.saveLifeGoalsFile(f)
	return &g, true
}

func (s *Server) deleteLifeGoal(id string) bool {
	lifeGoalMu.Lock()
	defer lifeGoalMu.Unlock()
	f := s.loadLifeGoalsFile()
	needle := strings.ToLower(strings.TrimSpace(id))
	kept := f.Goals[:0]
	found := false
	for _, g := range f.Goals {
		if !found && (strings.EqualFold(g.ID, needle) || strings.EqualFold(strings.TrimSpace(g.Title), needle)) {
			found = true
			continue
		}
		kept = append(kept, g)
	}
	if !found {
		return false
	}
	f.Goals = kept
	_ = s.saveLifeGoalsFile(f)
	return true
}

func (s *Server) clearLifeActivity() {
	lifeGoalMu.Lock()
	defer lifeGoalMu.Unlock()
	f := s.loadLifeGoalsFile()
	f.LastSteps = []map[string]any{}
	_ = s.saveLifeGoalsFile(f)
}

func (s *Server) handleListGoals(w http.ResponseWriter, _ *http.Request) {
	lifeGoalMu.Lock()
	f := s.loadLifeGoalsFile()
	lifeGoalMu.Unlock()
	goals := make([]map[string]any, 0, len(f.Goals))
	for _, g := range f.Goals {
		goals = append(goals, g.toPublic())
	}
	var last any
	if len(f.LastSteps) > 0 {
		last = f.LastSteps[len(f.LastSteps)-1]
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"goals":       goals,
		"life_folder": s.visibleLifeDir(),
		"last_step":   last,
		"digest":      "",
	})
}

func (s *Server) handleCreateGoal(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Title         string `json:"title"`
		Description   string `json:"description"`
		Why           string `json:"why"`
		Horizon       string `json:"horizon"`
		NextAction    string `json:"next_action"`
		DoneLooksLike string `json:"done_looks_like"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	why := strings.TrimSpace(req.Why)
	if why == "" {
		why = strings.TrimSpace(req.Description)
	}
	g, err := s.addLifeGoal(req.Title, why, req.Horizon, req.NextAction, req.DoneLooksLike, "api")
	if err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, g.toPublic())
}

func (s *Server) handlePatchGoal(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.PathValue("goal_id"))
	var fields map[string]any
	if err := json.NewDecoder(r.Body).Decode(&fields); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	// Drop nulls so "nothing to patch" matches Python exclude_none.
	clean := map[string]any{}
	for k, v := range fields {
		if v != nil {
			clean[k] = v
		}
	}
	if len(clean) == 0 {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "nothing to patch"})
		return
	}
	g, ok := s.patchLifeGoal(id, clean)
	if !ok {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "goal not found"})
		return
	}
	writeJSON(w, http.StatusOK, g.toPublic())
}

func (s *Server) handleDeleteGoal(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.PathValue("goal_id"))
	if !s.deleteLifeGoal(id) {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "goal not found"})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "deleted": id})
}

func (s *Server) handleClearGoalActivity(w http.ResponseWriter, _ *http.Request) {
	s.clearLifeActivity()
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) lifeFolderPath() string {
	return s.visibleLifeDir()
}
