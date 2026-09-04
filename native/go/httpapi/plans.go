package httpapi

import (
	"encoding/json"
	"net/http"
	"os"
	"path/filepath"
	"regexp"
	"sort"
	"strconv"
	"strings"
	"time"
)

var planIDSafe = regexp.MustCompile(`[^a-zA-Z0-9_-]`)

type planStep struct {
	ID          string   `json:"id"`
	Title       string   `json:"title"`
	Detail      string   `json:"detail,omitempty"`
	Status      string   `json:"status"`
	Risks       []string `json:"risks,omitempty"`
	Tools       []string `json:"tools,omitempty"`
	Intended    string   `json:"intended,omitempty"`
	Observed    string   `json:"observed,omitempty"`
	Evidence    string   `json:"evidence,omitempty"`
	BlockReason string   `json:"block_reason,omitempty"`
}

type taskPlan struct {
	ID        string         `json:"id"`
	Title     string         `json:"title"`
	Goal      string         `json:"goal"`
	Steps     []planStep     `json:"steps"`
	Risks     []string       `json:"risks"`
	SessionID *string        `json:"session_id"`
	Status    string         `json:"status"`
	CreatedAt string         `json:"created_at"`
	UpdatedAt string         `json:"updated_at"`
	Metadata  map[string]any `json:"metadata,omitempty"`
}

func utcNowISO() string {
	return time.Now().UTC().Format(time.RFC3339Nano)
}

func (s *Server) plansRoot() string {
	return filepath.Join(s.remedyHomeDir(), "plans")
}

func planPath(root, id string) string {
	safe := planIDSafe.ReplaceAllString(id, "")
	if safe == "" {
		safe = "plan"
	}
	return filepath.Join(root, safe+".json")
}

func (p *taskPlan) toDict() map[string]any {
	steps := make([]map[string]any, 0, len(p.Steps))
	for _, st := range p.Steps {
		m := map[string]any{
			"id":     st.ID,
			"title":  st.Title,
			"status": st.Status,
		}
		if st.Detail != "" {
			m["detail"] = st.Detail
		}
		if len(st.Risks) > 0 {
			m["risks"] = st.Risks
		}
		if len(st.Tools) > 0 {
			m["tools"] = st.Tools
		}
		if st.Intended != "" {
			m["intended"] = st.Intended
		}
		if st.Observed != "" {
			m["observed"] = st.Observed
		}
		if st.Evidence != "" {
			m["evidence"] = st.Evidence
		}
		if st.BlockReason != "" {
			m["block_reason"] = st.BlockReason
		}
		steps = append(steps, m)
	}
	var sid any
	if p.SessionID != nil {
		sid = *p.SessionID
	}
	meta := p.Metadata
	if meta == nil {
		meta = map[string]any{}
	}
	risks := p.Risks
	if risks == nil {
		risks = []string{}
	}
	return map[string]any{
		"id":         p.ID,
		"title":      p.Title,
		"goal":       p.Goal,
		"steps":      steps,
		"risks":      risks,
		"session_id": sid,
		"status":     p.Status,
		"created_at": p.CreatedAt,
		"updated_at": p.UpdatedAt,
		"metadata":   meta,
	}
}

func (p *taskPlan) summaryMarkdown() string {
	var b strings.Builder
	b.WriteString("# Plan: ")
	b.WriteString(p.Title)
	b.WriteString("\n**Status:** ")
	b.WriteString(p.Status)
	b.WriteString("\n")
	if p.Goal != "" {
		b.WriteString("**Goal:** ")
		b.WriteString(p.Goal)
		b.WriteString("\n")
	}
	b.WriteString("\n## Steps\n")
	for i, step := range p.Steps {
		mark := "[ ]"
		switch step.Status {
		case "done":
			mark = "[x]"
		case "active":
			mark = "[>]"
		case "skipped":
			mark = "[-]"
		}
		b.WriteString(strconv.Itoa(i+1) + ". " + mark + " **" + step.Title + "**\n")
	}
	b.WriteString("\n_Switch to **Build** mode to execute. Approve the plan first if it is still draft._\n")
	return b.String()
}

func parsePlanFile(path string) (*taskPlan, error) {
	var raw map[string]any
	if err := readJSONFile(path, &raw); err != nil {
		return nil, err
	}
	return planFromDict(raw), nil
}

func planFromDict(raw map[string]any) *taskPlan {
	stepsRaw, _ := raw["steps"].([]any)
	steps := make([]planStep, 0, len(stepsRaw))
	for i, s := range stepsRaw {
		switch v := s.(type) {
		case string:
			steps = append(steps, planStep{ID: "s" + strconv.Itoa(i+1), Title: v, Status: "pending"})
		case map[string]any:
			id, _ := v["id"].(string)
			if id == "" {
				id = "s" + strconv.Itoa(i+1)
			}
			title, _ := v["title"].(string)
			if title == "" {
				title = "step"
			}
			st, _ := v["status"].(string)
			if st == "" {
				st = "pending"
			}
			steps = append(steps, planStep{
				ID:          id,
				Title:       title,
				Detail:      asString(v["detail"]),
				Status:      st,
				Intended:    asString(v["intended"]),
				Observed:    asString(v["observed"]),
				Evidence:    asString(v["evidence"]),
				BlockReason: asString(v["block_reason"]),
			})
		}
	}
	id := asString(raw["id"])
	if id == "" {
		id = shortHexID(12)
	}
	title := asString(raw["title"])
	if title == "" {
		title = "Untitled plan"
	}
	status := asString(raw["status"])
	if status == "" {
		status = "draft"
	}
	var sid *string
	if v := asString(raw["session_id"]); v != "" {
		sid = &v
	}
	created := asString(raw["created_at"])
	if created == "" {
		created = utcNowISO()
	}
	updated := asString(raw["updated_at"])
	if updated == "" {
		updated = created
	}
	risks := stringList(raw["risks"])
	meta, _ := raw["metadata"].(map[string]any)
	return &taskPlan{
		ID:        id,
		Title:     title,
		Goal:      asString(raw["goal"]),
		Steps:     steps,
		Risks:     risks,
		SessionID: sid,
		Status:    status,
		CreatedAt: created,
		UpdatedAt: updated,
		Metadata:  meta,
	}
}

func asString(v any) string {
	if v == nil {
		return ""
	}
	switch t := v.(type) {
	case string:
		return t
	default:
		return strings.TrimSpace(stringifyAny(t))
	}
}

func stringifyAny(v any) string {
	b, err := json.Marshal(v)
	if err != nil {
		return ""
	}
	s := string(b)
	if len(s) >= 2 && s[0] == '"' {
		var out string
		if json.Unmarshal(b, &out) == nil {
			return out
		}
	}
	return s
}

func stringList(v any) []string {
	arr, ok := v.([]any)
	if !ok {
		return []string{}
	}
	out := make([]string, 0, len(arr))
	for _, x := range arr {
		if s := strings.TrimSpace(asString(x)); s != "" {
			out = append(out, s)
		}
	}
	return out
}

func (s *Server) savePlan(p *taskPlan) error {
	p.UpdatedAt = utcNowISO()
	root := s.plansRoot()
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	return writeJSONAtomic(planPath(root, p.ID), p.toDict())
}

func (s *Server) getPlan(id string) *taskPlan {
	path := planPath(s.plansRoot(), id)
	p, err := parsePlanFile(path)
	if err != nil {
		return nil
	}
	return p
}

func (s *Server) listPlans(sessionID string, limit int) []*taskPlan {
	if limit < 1 {
		limit = 1
	}
	if limit > 100 {
		limit = 100
	}
	root := s.plansRoot()
	ents, err := os.ReadDir(root)
	if err != nil {
		return nil
	}
	type dated struct {
		mtime time.Time
		path  string
	}
	files := make([]dated, 0, len(ents))
	for _, e := range ents {
		if e.IsDir() || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		files = append(files, dated{mtime: info.ModTime(), path: filepath.Join(root, e.Name())})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mtime.After(files[j].mtime) })
	out := make([]*taskPlan, 0, limit)
	for _, f := range files {
		p, err := parsePlanFile(f.path)
		if err != nil {
			continue
		}
		if sessionID != "" {
			if p.SessionID == nil || *p.SessionID != sessionID {
				continue
			}
		}
		out = append(out, p)
		if len(out) >= limit {
			break
		}
	}
	return out
}

func (s *Server) latestPlan(sessionID string, actionableOnly bool) *taskPlan {
	limit := 1
	if actionableOnly {
		limit = 50
	}
	plans := s.listPlans(sessionID, limit)
	if actionableOnly {
		filtered := plans[:0]
		for _, p := range plans {
			st := strings.ToLower(p.Status)
			if st == "done" || st == "cancelled" {
				continue
			}
			filtered = append(filtered, p)
		}
		plans = filtered
	}
	if len(plans) == 0 {
		return nil
	}
	return plans[0]
}

func (s *Server) createPlan(title, goal string, steps []any, risks []string, sessionID *string, status string) *taskPlan {
	stepObjs := make([]planStep, 0, len(steps))
	for i, raw := range steps {
		switch v := raw.(type) {
		case string:
			t := strings.TrimSpace(v)
			if t == "" {
				t = "Step " + strconv.Itoa(i+1)
			}
			stepObjs = append(stepObjs, planStep{ID: "s" + strconv.Itoa(i+1), Title: t, Status: "pending"})
		case map[string]any:
			id := asString(v["id"])
			if id == "" {
				id = "s" + strconv.Itoa(i+1)
			}
			t := asString(v["title"])
			if t == "" {
				t = "Step " + strconv.Itoa(i+1)
			}
			st := asString(v["status"])
			if st == "" {
				st = "pending"
			}
			stepObjs = append(stepObjs, planStep{ID: id, Title: t, Status: st, Detail: asString(v["detail"])})
		}
	}
	st := strings.ToLower(strings.TrimSpace(status))
	switch st {
	case "draft", "approved", "active", "done", "cancelled":
	default:
		st = "draft"
	}
	allPending := len(stepObjs) == 0
	if !allPending {
		allPending = true
		for _, x := range stepObjs {
			if x.Status != "pending" {
				allPending = false
				break
			}
		}
	}
	if (st == "done" || st == "cancelled") && allPending {
		st = "draft"
	}
	if sessionID != nil && *sessionID != "" {
		for _, old := range s.listPlans(*sessionID, 50) {
			ost := strings.ToLower(old.Status)
			if ost == "draft" || ost == "approved" || ost == "active" {
				old.Status = "cancelled"
				_ = s.savePlan(old)
			}
		}
	}
	titleS := strings.TrimSpace(title)
	if titleS == "" {
		titleS = "Untitled plan"
	}
	goalS := strings.TrimSpace(goal)
	if goalS == "" {
		goalS = titleS
	}
	if risks == nil {
		risks = []string{}
	}
	p := &taskPlan{
		ID:        shortHexID(12),
		Title:     truncateRunes(titleS, 200),
		Goal:      truncateRunes(goalS, 2000),
		Steps:     stepObjs,
		Risks:     risks,
		SessionID: sessionID,
		Status:    st,
		CreatedAt: utcNowISO(),
		Metadata:  map[string]any{},
	}
	_ = s.savePlan(p)
	return p
}

func truncateRunes(s string, n int) string {
	r := []rune(s)
	if len(r) <= n {
		return s
	}
	return string(r[:n])
}

func (s *Server) setPlanStatus(id, status string) *taskPlan {
	st := strings.ToLower(strings.TrimSpace(status))
	switch st {
	case "draft", "approved", "active", "done", "cancelled":
	default:
		return nil
	}
	p := s.getPlan(id)
	if p == nil {
		return nil
	}
	p.Status = st
	if err := s.savePlan(p); err != nil {
		return nil
	}
	return p
}

func findPlanStep(p *taskPlan, needle string) *planStep {
	n := strings.TrimSpace(needle)
	if n == "" || p == nil {
		return nil
	}
	nl := strings.ToLower(n)
	for i := range p.Steps {
		if p.Steps[i].ID == n || strings.ToLower(p.Steps[i].ID) == nl {
			return &p.Steps[i]
		}
	}
	re := regexp.MustCompile(`(?i)^(?:s(?:tep)?\s*)?(\d+)$`)
	if m := re.FindStringSubmatch(n); len(m) == 2 {
		idx, _ := strconv.Atoi(m[1])
		idx--
		if idx >= 0 && idx < len(p.Steps) {
			return &p.Steps[idx]
		}
	}
	for i := range p.Steps {
		if strings.ToLower(p.Steps[i].Title) == nl {
			return &p.Steps[i]
		}
	}
	return nil
}

func (s *Server) updatePlanStepStatus(planID, stepID, status string, planStatus string) *taskPlan {
	st := strings.ToLower(strings.TrimSpace(status))
	switch st {
	case "pending", "active", "done", "skipped":
	default:
		return nil
	}
	p := s.getPlan(planID)
	if p == nil {
		return nil
	}
	target := findPlanStep(p, stepID)
	if target == nil {
		return nil
	}
	target.Status = st
	if st == "skipped" && target.BlockReason == "" {
		target.BlockReason = "skipped"
	}
	if st == "active" || st == "done" || st == "skipped" {
		if p.Status == "draft" || p.Status == "approved" {
			p.Status = "active"
		}
	}
	if len(p.Steps) > 0 {
		allDone := true
		for _, x := range p.Steps {
			if x.Status != "done" && x.Status != "skipped" {
				allDone = false
				break
			}
		}
		if allDone {
			p.Status = "done"
		} else if st == "active" && p.Status == "done" {
			p.Status = "active"
		}
	}
	pst := strings.ToLower(strings.TrimSpace(planStatus))
	switch pst {
	case "draft", "approved", "active", "done", "cancelled":
		p.Status = pst
	}
	if err := s.savePlan(p); err != nil {
		return nil
	}
	return p
}

func planResponse(p *taskPlan) map[string]any {
	if p == nil {
		return map[string]any{"plan": nil}
	}
	return map[string]any{"plan": p.toDict(), "markdown": p.summaryMarkdown()}
}

func (s *Server) handleListPlans(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	limit := 30
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			limit = n
		}
	}
	plans := s.listPlans(sid, limit)
	out := make([]map[string]any, 0, len(plans))
	for _, p := range plans {
		out = append(out, p.toDict())
	}
	writeJSON(w, http.StatusOK, map[string]any{"plans": out})
}

func (s *Server) handleLatestPlan(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	actionable := false
	av := strings.ToLower(strings.TrimSpace(r.URL.Query().Get("actionable")))
	if av == "1" || av == "true" || av == "yes" {
		actionable = true
	}
	p := s.latestPlan(sid, actionable)
	writeJSON(w, http.StatusOK, planResponse(p))
}

func (s *Server) handleGetPlan(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.PathValue("plan_id"))
	p := s.getPlan(id)
	if p == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Plan not found"})
		return
	}
	writeJSON(w, http.StatusOK, planResponse(p))
}

func (s *Server) handleCreatePlan(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Title     string `json:"title"`
		Goal      string `json:"goal"`
		Steps     []any  `json:"steps"`
		Risks     []any  `json:"risks"`
		SessionID *string `json:"session_id"`
		Status    string `json:"status"`
	}
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	if strings.TrimSpace(req.Title) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "title required"})
		return
	}
	p := s.createPlan(req.Title, req.Goal, req.Steps, stringList(req.Risks), req.SessionID, req.Status)
	writeJSON(w, http.StatusOK, planResponse(p))
}

func (s *Server) handleSetPlanStatus(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.PathValue("plan_id"))
	var req struct {
		Status string `json:"status"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	p := s.setPlanStatus(id, req.Status)
	if p == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Plan not found or invalid status"})
		return
	}
	writeJSON(w, http.StatusOK, planResponse(p))
}

func (s *Server) handleSetPlanStepStatus(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.PathValue("plan_id"))
	var req struct {
		Status     string `json:"status"`
		StepID     string `json:"step_id"`
		PlanStatus string `json:"plan_status"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	if strings.TrimSpace(req.StepID) == "" {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "step_id is required"})
		return
	}
	st := strings.ToLower(strings.TrimSpace(req.Status))
	switch st {
	case "pending", "active", "done", "skipped":
	default:
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "status must be pending | active | done | skipped"})
		return
	}
	if s.getPlan(id) == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Plan not found"})
		return
	}
	p := s.updatePlanStepStatus(id, req.StepID, st, req.PlanStatus)
	if p == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Step not found or invalid status"})
		return
	}
	writeJSON(w, http.StatusOK, planResponse(p))
}
