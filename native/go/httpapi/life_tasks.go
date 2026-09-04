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
	"sync"
	"time"
)

var lifeTaskIDSafe = regexp.MustCompile(`^[a-zA-Z0-9._-]{4,80}$`)

type lifeTaskHub struct {
	mu         sync.Mutex
	bySession  map[string]map[string]any
	latest     map[string]any
}

func newLifeTaskHub() *lifeTaskHub {
	return &lifeTaskHub{bySession: map[string]map[string]any{}}
}

func (h *lifeTaskHub) Publish(payload map[string]any, sessionID string) map[string]any {
	h.mu.Lock()
	defer h.mu.Unlock()
	card := copyMap(payload)
	sid := strings.TrimSpace(sessionID)
	if sid == "" {
		sid = strings.TrimSpace(asString(card["session_id"]))
	}
	if sid == "" {
		sid = "default"
	}
	card["session_id"] = sid
	card["updated_at"] = float64(time.Now().UnixNano()) / 1e9
	h.bySession[sid] = card
	h.latest = card
	return copyMap(card)
}

func (h *lifeTaskHub) Current(sessionID string) map[string]any {
	h.mu.Lock()
	defer h.mu.Unlock()
	if sessionID != "" {
		if hit, ok := h.bySession[strings.TrimSpace(sessionID)]; ok {
			return copyMap(hit)
		}
		return nil
	}
	if h.latest == nil {
		return nil
	}
	return copyMap(h.latest)
}

func (h *lifeTaskHub) Clear(sessionID string) {
	h.mu.Lock()
	defer h.mu.Unlock()
	if sessionID != "" {
		sid := strings.TrimSpace(sessionID)
		delete(h.bySession, sid)
		if h.latest != nil && asString(h.latest["session_id"]) == sid {
			h.latest = nil
		}
		return
	}
	h.bySession = map[string]map[string]any{}
	h.latest = nil
}

func copyMap(in map[string]any) map[string]any {
	if in == nil {
		return nil
	}
	out := make(map[string]any, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

func sseLifeCard(card map[string]any) map[string]any {
	if card == nil {
		return map[string]any{}
	}
	stepsOut := make([]map[string]any, 0)
	if steps, ok := card["steps"].([]any); ok {
		for _, s := range steps {
			m, ok := s.(map[string]any)
			if !ok {
				continue
			}
			stepsOut = append(stepsOut, map[string]any{
				"title":         asString(m["title"]),
				"status":        asString(m["status"]),
				"observed":      truncateRunes(asString(m["observed"]), 240),
				"intended":      truncateRunes(asString(m["intended"]), 240),
				"block_reason":  asString(m["block_reason"]),
				"evidence_hash": asString(m["evidence_hash"]),
				"screenshot":    asString(m["screenshot"]),
			})
			if len(stepsOut) >= 20 {
				break
			}
		}
	} else if steps, ok := card["steps"].([]map[string]any); ok {
		for _, m := range steps {
			stepsOut = append(stepsOut, map[string]any{
				"title":         asString(m["title"]),
				"status":        asString(m["status"]),
				"observed":      truncateRunes(asString(m["observed"]), 240),
				"intended":      truncateRunes(asString(m["intended"]), 240),
				"block_reason":  asString(m["block_reason"]),
				"evidence_hash": asString(m["evidence_hash"]),
				"screenshot":    asString(m["screenshot"]),
			})
			if len(stepsOut) >= 20 {
				break
			}
		}
	}
	keys := []string{
		"task_id", "goal", "status", "ok", "spoken", "step", "total", "title",
		"approval_id", "choices", "checkpoint", "kind", "session_id", "updated_at", "handoff",
	}
	out := map[string]any{}
	for _, k := range keys {
		v, ok := card[k]
		if !ok || v == nil || v == "" {
			continue
		}
		if m, ok := v.(map[string]any); ok && len(m) == 0 {
			continue
		}
		out[k] = v
	}
	out["steps"] = stepsOut
	if ch, ok := card["choices"]; ok && ch != nil {
		out["choices"] = ch
	}
	if hand, ok := card["handoff"].(map[string]any); ok && len(hand) > 0 {
		out["handoff"] = hand
	}
	return out
}

func (s *Server) lifeTasksRoot() string {
	return filepath.Join(s.remedyHomeDir(), "life_tasks")
}

func (s *Server) loadLifeTask(taskID string) map[string]any {
	tid := strings.TrimSpace(taskID)
	if !lifeTaskIDSafe.MatchString(tid) {
		return nil
	}
	path := filepath.Join(s.lifeTasksRoot(), tid+".json")
	var raw map[string]any
	if err := readJSONFile(path, &raw); err != nil {
		return nil
	}
	return raw
}

func (s *Server) saveLifeTaskRec(rec map[string]any) error {
	tid := strings.TrimSpace(asString(rec["id"]))
	if !lifeTaskIDSafe.MatchString(tid) {
		return goalError("invalid life-task id")
	}
	root := s.lifeTasksRoot()
	if err := os.MkdirAll(root, 0o700); err != nil {
		return err
	}
	rec["updated_at"] = utcNowISO()
	return writeJSONAtomic(filepath.Join(root, tid+".json"), rec)
}

func (s *Server) listLifeTaskRows(sessionID string, limit int) []map[string]any {
	if limit < 1 {
		limit = 1
	}
	if limit > 50 {
		limit = 50
	}
	root := s.lifeTasksRoot()
	ents, err := os.ReadDir(root)
	if err != nil {
		return []map[string]any{}
	}
	type dated struct {
		mtime time.Time
		name  string
	}
	files := make([]dated, 0)
	for _, e := range ents {
		if e.IsDir() || !strings.HasPrefix(e.Name(), "lt_") || !strings.HasSuffix(e.Name(), ".json") {
			continue
		}
		info, err := e.Info()
		if err != nil {
			continue
		}
		files = append(files, dated{mtime: info.ModTime(), name: e.Name()})
	}
	sort.Slice(files, func(i, j int) bool { return files[i].mtime.After(files[j].mtime) })
	out := make([]map[string]any, 0, limit)
	for _, f := range files {
		stem := strings.TrimSuffix(f.name, ".json")
		rec := s.loadLifeTask(stem)
		if rec == nil {
			continue
		}
		if sessionID != "" && asString(rec["session_id"]) != sessionID {
			continue
		}
		stepsN := 0
		switch v := rec["steps"].(type) {
		case []any:
			stepsN = len(v)
		case []map[string]any:
			stepsN = len(v)
		}
		out = append(out, map[string]any{
			"id":         rec["id"],
			"goal":       rec["goal"],
			"status":     rec["status"],
			"ok":         rec["ok"],
			"updated_at": rec["updated_at"],
			"steps":      stepsN,
		})
		if len(out) >= limit {
			break
		}
	}
	return out
}

func explainLifePlan(goal, markdown string) string {
	g := strings.TrimSpace(goal)
	md := strings.TrimSpace(markdown)
	if g != "" && md != "" {
		return "Plan for " + g + ": " + truncateRunes(md, 400)
	}
	if md != "" {
		return truncateRunes(md, 400)
	}
	if g != "" {
		return "Remedy proposed a plan for: " + g + ". Yes continues, No stops, Explain restates the plan."
	}
	return "Remedy proposed a computer plan. Yes continues, No stops."
}

func (s *Server) handleCurrentLifeTask(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	card := s.lifeHub.Current(sid)
	pending := s.approvals.ListPending(sid)
	var approval map[string]any
	for _, it := range pending {
		if it.ToolName == "life_drive" {
			approval = s.approvals.ToPublic(it)
			break
		}
	}
	if card == nil && approval != nil {
		card = map[string]any{
			"status":      "need_you",
			"spoken":      asString(approval["summary"]),
			"approval_id": approval["id"],
			"kind":        "plan_gate",
			"choices":     []string{"yes", "no", "explain"},
			"checkpoint":  false,
		}
		if sid != "" {
			card["session_id"] = sid
		}
	}
	var task any
	if card != nil {
		task = sseLifeCard(card)
	}
	writeJSON(w, http.StatusOK, map[string]any{"task": task, "approval": approval})
}

func (s *Server) handleListLifeTasks(w http.ResponseWriter, r *http.Request) {
	sid := strings.TrimSpace(r.URL.Query().Get("session_id"))
	limit := 20
	if v := r.URL.Query().Get("limit"); v != "" {
		if n, err := strconv.Atoi(v); err == nil {
			limit = n
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"tasks": s.listLifeTaskRows(sid, limit)})
}

func (s *Server) handleGetLifeTask(w http.ResponseWriter, r *http.Request) {
	id := strings.TrimSpace(r.PathValue("task_id"))
	rec := s.loadLifeTask(id)
	if rec == nil {
		writeJSON(w, http.StatusNotFound, map[string]string{"detail": "Life task not found"})
		return
	}
	writeJSON(w, http.StatusOK, rec)
}

func (s *Server) handleActLifeTask(w http.ResponseWriter, r *http.Request) {
	var req struct {
		Action     string  `json:"action"`
		SessionID  *string `json:"session_id"`
		TaskID     *string `json:"task_id"`
		ApprovalID *string `json:"approval_id"`
	}
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		writeJSON(w, http.StatusBadRequest, map[string]string{"detail": "invalid JSON body"})
		return
	}
	act := strings.ToLower(strings.TrimSpace(req.Action))
	var sid string
	if req.SessionID != nil {
		sid = strings.TrimSpace(*req.SessionID)
	}
	card := s.lifeHub.Current(sid)
	tid := ""
	if req.TaskID != nil {
		tid = strings.TrimSpace(*req.TaskID)
	}
	if tid == "" && card != nil {
		tid = asString(card["task_id"])
	}
	appr := ""
	if req.ApprovalID != nil {
		appr = strings.TrimSpace(*req.ApprovalID)
	}
	if appr == "" && card != nil {
		appr = asString(card["approval_id"])
	}
	kind := ""
	if card != nil {
		kind = asString(card["kind"])
	}

	if act == "explain" || act == "why" {
		md := ""
		goal := ""
		if card != nil {
			md = asString(card["markdown"])
			goal = asString(card["goal"])
		}
		if tid != "" {
			if rec := s.loadLifeTask(tid); rec != nil {
				if v := asString(rec["markdown"]); v != "" {
					md = v
				}
				if v := asString(rec["goal"]); v != "" {
					goal = v
				}
			}
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":     true,
			"action": "explain",
			"spoken": explainLifePlan(goal, md),
			"task":   sseLifeCard(card),
		})
		return
	}

	if act == "no" || act == "deny" || act == "cancel" {
		if appr != "" {
			s.approvals.Resolve(appr, false, "session")
		}
		out := map[string]any{"ok": true, "status": "cancelled", "steps": []any{}, "goal": ""}
		if tid != "" {
			if rec := s.loadLifeTask(tid); rec != nil {
				rec["status"] = "cancelled"
				rec["ok"] = false
				_ = s.saveLifeTaskRec(rec)
				out = rec
			}
			s.lifeHub.Clear(sid)
		} else {
			s.lifeHub.Clear(sid)
		}
		taskCard := map[string]any{}
		for k, v := range out {
			taskCard[k] = v
		}
		taskCard["status"] = "cancelled"
		taskCard["spoken"] = "Stopped."
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":     true,
			"action": "no",
			"spoken": "Stopped. Nothing else was pressed.",
			"task":   sseLifeCard(taskCard),
		})
		return
	}

	if act == "yes" || act == "resume" || act == "continue" {
		if appr != "" {
			s.approvals.Resolve(appr, true, "session")
		}
		// Native runtime does not drive computer_* tools yet — resolve the
		// gate and ask the partner to continue from chat (Python parity fallback).
		spoken := "Yes. Ask Remedy to continue."
		if kind == "plan_gate" {
			spoken = "Yes — plan approved. Ask Remedy to continue the drive."
		}
		if card != nil {
			card = copyMap(card)
			card["spoken"] = spoken
			if kind == "plan_gate" {
				card["status"] = "approved"
			}
			s.lifeHub.Publish(card, sid)
		}
		writeJSON(w, http.StatusOK, map[string]any{
			"ok":     true,
			"action": "yes",
			"spoken": spoken,
			"task":   sseLifeCard(s.lifeHub.Current(sid)),
			"result": map[string]any{"status": "approved", "task_id": tid},
		})
		return
	}

	writeJSON(w, http.StatusOK, map[string]any{
		"ok":     false,
		"action": act,
		"spoken": "Say Yes, No, or Explain.",
		"task":   sseLifeCard(card),
	})
}

func (s *Server) handleProbeLifeTask(w http.ResponseWriter, r *http.Request) {
	var req struct {
		SessionID *string `json:"session_id"`
		TaskID    *string `json:"task_id"`
		PageText  string  `json:"page_text"`
		URL       string  `json:"url"`
		RailReady *bool   `json:"rail_ready"`
	}
	_ = json.NewDecoder(r.Body).Decode(&req)
	var sid string
	if req.SessionID != nil {
		sid = strings.TrimSpace(*req.SessionID)
	}
	card := s.lifeHub.Current(sid)
	if card == nil || asString(card["status"]) != "need_you" {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": true, "cleared": false, "task": sseLifeCard(card),
		})
		return
	}
	hand, _ := card["handoff"].(map[string]any)
	kind := asString(hand["kind"])
	// Auto-resume kinds (captcha/password) need host bridge — not wired in Go yet.
	auto := kind == "captcha" || kind == "password" || kind == "challenge" || kind == "2fa"
	if !auto {
		writeJSON(w, http.StatusOK, map[string]any{
			"ok": true, "cleared": false, "reason": "needs_yes", "task": sseLifeCard(card),
		})
		return
	}
	ready := false
	if req.RailReady != nil {
		ready = *req.RailReady
	}
	spoken := "Open the Browser rail, then finish the sign-in or CAPTCHA. Remedy will continue after."
	if ready {
		spoken = "The Browser rail is ready. Finish the sign-in or CAPTCHA, then Remedy will continue."
	}
	card = copyMap(card)
	card["spoken"] = spoken
	s.lifeHub.Publish(card, sid)
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true, "cleared": false, "spoken": spoken, "task": sseLifeCard(card),
	})
}
