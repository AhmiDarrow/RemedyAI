package httpapi

import (
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"
)

// HTTP surface for Grove: one overview read plus the owner's controls.

func (s *Server) registerPartnerRoutes() {
	s.mux.HandleFunc("GET /api/partner/head", s.handlePartnerHead)
	s.mux.HandleFunc("POST /api/partner/head/session", s.handlePartnerHeadSession)
	s.mux.HandleFunc("PUT /api/partner/heartbeat", s.handlePartnerHeartbeatPut)
	s.mux.HandleFunc("POST /api/partner/heartbeat/run", s.handlePartnerHeartbeatRun)
	s.mux.HandleFunc("GET /api/partner/routines", s.handlePartnerRoutinesList)
	s.mux.HandleFunc("POST /api/partner/routines", s.handlePartnerRoutinesAdd)
	s.mux.HandleFunc("PATCH /api/partner/routines/{id}", s.handlePartnerRoutinePatch)
	s.mux.HandleFunc("DELETE /api/partner/routines/{id}", s.handlePartnerRoutineDelete)
	s.mux.HandleFunc("POST /api/partner/routines/{id}/run", s.handlePartnerRoutineRun)
	s.mux.HandleFunc("PUT /api/partner/goals/{goal_id}", s.handlePartnerGoalPut)
	s.mux.HandleFunc("POST /api/partner/goals/{goal_id}/step", s.handlePartnerGoalStep)
	s.mux.HandleFunc("POST /api/partner/children", s.handlePartnerChildSpawn)
	s.mux.HandleFunc("POST /api/partner/children/{id}/message", s.handlePartnerChildMessage)
	s.mux.HandleFunc("POST /api/partner/children/{id}/stop", s.handlePartnerChildStop)
	s.mux.HandleFunc("DELETE /api/partner/children/{id}", s.handlePartnerChildForget)
	s.mux.HandleFunc("PUT /api/partner/channel", s.handlePartnerChannelPut)
	s.mux.HandleFunc("POST /api/partner/activity/seen", s.handlePartnerActivitySeen)
}

func decodePartnerBody(r *http.Request, dst any) error {
	dec := json.NewDecoder(io.LimitReader(r.Body, 1<<20))
	if err := dec.Decode(dst); err != nil && err != io.EOF {
		return err
	}
	return nil
}

func partnerFail(w http.ResponseWriter, status int, msg string) {
	writeJSON(w, status, map[string]any{"ok": false, "detail": msg})
}

// handlePartnerHead is everything Grove draws about Remedy and her children.
func (s *Server) handlePartnerHead(w http.ResponseWriter, _ *http.Request) {
	h := s.partner
	if h == nil {
		partnerFail(w, http.StatusServiceUnavailable, "not available")
		return
	}
	capa := s.partnerCapacity()
	h.mu.Lock()
	st := h.st
	headID := st.HeadSessionID
	children := []map[string]any{}
	for _, c := range h.sortedChildrenLocked() {
		row := childPublic(*c)
		row["session_id"] = c.SessionID
		row["parent_session_id"] = c.ParentSessionID
		row["updated_at"] = c.UpdatedAt
		row["runs"] = c.Runs
		if _, running := h.running[c.SessionID]; running {
			row["state"] = partnerStateWorking
		}
		children = append(children, row)
	}
	goals := map[string]any{}
	for id, g := range st.Goals {
		row := map[string]any{
			"goal_id":       g.GoalID,
			"session_id":    g.SessionID,
			"autopilot":     g.Autopilot,
			"every_minutes": g.EveryMinutes,
			"state":         g.State,
			"last_update":   g.LastUpdate,
			"last_run_at":   g.LastRunAt,
			"runs":          g.Runs,
		}
		if _, running := h.running[g.SessionID]; running && g.SessionID != "" {
			row["state"] = partnerStateWorking
		}
		goals[id] = row
	}
	routines := []map[string]any{}
	for _, r := range st.Routines {
		routines = append(routines, routinePublic(*r))
	}
	activity := make([]partnerActivity, len(st.Activity))
	copy(activity, st.Activity)
	// Newest first for the timeline.
	for i, j := 0, len(activity)-1; i < j; i, j = i+1, j-1 {
		activity[i], activity[j] = activity[j], activity[i]
	}
	if len(activity) > 60 {
		activity = activity[:60]
	}
	unseen := 0
	for _, a := range st.Activity {
		if a.TS > st.ActivitySeen {
			unseen++
		}
	}
	headState := partnerStateIdle
	if _, running := h.running[headID]; running && headID != "" {
		headState = partnerStateWorking
	}
	queued := []map[string]any{}
	for _, q := range h.queue {
		queued = append(queued, map[string]any{"kind": q.Kind, "label": q.Label, "session_id": q.SessionID, "queued_at": q.QueuedAt.UTC().Format(time.RFC3339)})
	}
	hb := st.Heartbeat
	var owner any
	if st.OwnerChannel != nil {
		owner = *st.OwnerChannel
	}
	recent := append([]partnerChannel(nil), st.RecentChats...)
	h.mu.Unlock()
	if headID != "" && s.claims != nil && s.claims.IsClaimed(headID) {
		headState = partnerStateWorking
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok": true,
		"head": map[string]any{
			"session_id": headID,
			"state":      headState,
		},
		"capacity":        capa,
		"capacity_note":   capa.Sentence(),
		"children":        children,
		"goals":           goals,
		"routines":        routines,
		"inner":           s.innerPublic(),
		"queue":           queued,
		"activity":        activity,
		"activity_unseen": unseen,
		"heartbeat": map[string]any{
			"enabled":       hb.Enabled,
			"every_minutes": hb.EveryMinutes,
			"quiet_start":   hb.QuietStart,
			"quiet_end":     hb.QuietEnd,
			"last_at":       hb.LastAt,
			"last_note":     hb.LastNote,
		},
		"channels": map[string]any{
			"owner":  owner,
			"recent": recent,
		},
	})
}

func (s *Server) handlePartnerHeadSession(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SessionID string `json:"session_id"`
	}
	if err := decodePartnerBody(r, &body); err != nil {
		partnerFail(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if sid := strings.TrimSpace(body.SessionID); sid != "" {
		h := s.partner
		h.mu.Lock()
		current := h.st.HeadSessionID
		h.mu.Unlock()
		// Adopt only when Remedy has no live head yet — Grove migrating its
		// remembered home must not replace a head another window made.
		if current == "" {
			if err := s.partnerAdoptHeadSession(sid); err == nil {
				writeJSON(w, http.StatusOK, map[string]any{"ok": true, "session_id": sid})
				return
			}
		} else if _, ok, _ := s.sessions.Get(current); !ok {
			if err := s.partnerAdoptHeadSession(sid); err == nil {
				writeJSON(w, http.StatusOK, map[string]any{"ok": true, "session_id": sid})
				return
			}
		}
	}
	sid, err := s.partnerEnsureHeadSession()
	if err != nil {
		partnerFail(w, http.StatusInternalServerError, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "session_id": sid})
}

func (s *Server) handlePartnerHeartbeatPut(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Enabled      *bool   `json:"enabled"`
		EveryMinutes *int    `json:"every_minutes"`
		QuietStart   *string `json:"quiet_start"`
		QuietEnd     *string `json:"quiet_end"`
	}
	if err := decodePartnerBody(r, &body); err != nil {
		partnerFail(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	for _, p := range []*string{body.QuietStart, body.QuietEnd} {
		if p != nil && strings.TrimSpace(*p) != "" {
			if _, _, err := parseClock(*p); err != nil {
				partnerFail(w, http.StatusBadRequest, err.Error())
				return
			}
		}
	}
	h := s.partner
	h.mu.Lock()
	hb := &h.st.Heartbeat
	if body.Enabled != nil {
		hb.Enabled = *body.Enabled
		if hb.Enabled {
			hb.LastAt = "" // look around right away
			hb.LastDigest = ""
		}
	}
	if body.EveryMinutes != nil {
		m := *body.EveryMinutes
		if m < partnerMinEveryMinutes {
			m = partnerMinEveryMinutes
		}
		if m > 24*60 {
			m = 24 * 60
		}
		hb.EveryMinutes = m
	}
	if body.QuietStart != nil {
		hb.QuietStart = strings.TrimSpace(*body.QuietStart)
	}
	if body.QuietEnd != nil {
		hb.QuietEnd = strings.TrimSpace(*body.QuietEnd)
	}
	out := *hb
	h.saveLocked()
	h.mu.Unlock()
	h.poke()
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "heartbeat": out})
}

func (s *Server) handlePartnerHeartbeatRun(w http.ResponseWriter, _ *http.Request) {
	sw, changed := s.partnerHeartbeatSweep(s.partner.now(), true)
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "note": sw.sentence(), "changed": changed})
}

func (s *Server) handlePartnerRoutinesList(w http.ResponseWriter, _ *http.Request) {
	out, _ := toolRoutinesList(s, partnerToolInput{})
	m := out.(map[string]any)
	m["ok"] = true
	writeJSON(w, http.StatusOK, m)
}

type partnerRoutineBody struct {
	Title    string           `json:"title"`
	What     string           `json:"what"`
	Mode     string           `json:"mode"`
	Schedule *partnerSchedule `json:"schedule"`
	Target   string           `json:"target"`
	Deliver  string           `json:"deliver"`
	Enabled  *bool            `json:"enabled"`
}

func (s *Server) handlePartnerRoutinesAdd(w http.ResponseWriter, r *http.Request) {
	var body partnerRoutineBody
	if err := decodePartnerBody(r, &body); err != nil {
		partnerFail(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if body.Schedule == nil {
		partnerFail(w, http.StatusBadRequest, "schedule is required")
		return
	}
	rt, err := s.partner.addRoutine(partnerRoutine{
		Title: body.Title, Prompt: body.What, Mode: body.Mode, Schedule: *body.Schedule,
		Target: body.Target, Deliver: body.Deliver, CreatedBy: "owner",
	})
	if err != nil {
		partnerFail(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "routine": routinePublic(*rt)})
}

func (s *Server) handlePartnerRoutinePatch(w http.ResponseWriter, r *http.Request) {
	var body partnerRoutineBody
	if err := decodePartnerBody(r, &body); err != nil {
		partnerFail(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	var sched partnerSchedule
	if body.Schedule != nil {
		sc, err := normalizeSchedule(*body.Schedule)
		if err != nil {
			partnerFail(w, http.StatusBadRequest, err.Error())
			return
		}
		sched = sc
	}
	h := s.partner
	h.mu.Lock()
	_, rt := h.findRoutineLocked(r.PathValue("id"))
	if rt == nil {
		h.mu.Unlock()
		partnerFail(w, http.StatusNotFound, "routine not found")
		return
	}
	if t := strings.TrimSpace(body.Title); t != "" {
		rt.Title = truncateRunes(t, 120)
	}
	if p := strings.TrimSpace(body.What); p != "" {
		rt.Prompt = truncateRunes(p, 4000)
	}
	if body.Mode != "" {
		rt.Mode = normalizeRoutineMode(body.Mode)
	}
	if body.Target != "" {
		rt.Target = normalizeRoutineTarget(body.Target)
	}
	if body.Deliver != "" {
		rt.Deliver = normalizeDeliver(body.Deliver)
	}
	reschedule := false
	if body.Schedule != nil {
		rt.Schedule = sched
		reschedule = true
	}
	if body.Enabled != nil {
		reschedule = reschedule || (*body.Enabled && !rt.Enabled)
		rt.Enabled = *body.Enabled
	}
	if reschedule {
		if next := nextRunAfter(rt.Schedule, h.now(), time.Local); !next.IsZero() {
			rt.NextRun = next.UTC().Format(time.RFC3339)
		} else {
			rt.NextRun = ""
		}
	}
	out := *rt
	h.saveLocked()
	h.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "routine": routinePublic(out)})
}

func (s *Server) handlePartnerRoutineDelete(w http.ResponseWriter, r *http.Request) {
	if !s.partnerDeleteRoutine(r.PathValue("id")) {
		partnerFail(w, http.StatusNotFound, "routine not found")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handlePartnerRoutineRun(w http.ResponseWriter, r *http.Request) {
	h := s.partner
	h.mu.Lock()
	_, rt := h.findRoutineLocked(r.PathValue("id"))
	var cp partnerRoutine
	if rt != nil {
		cp = *rt
	}
	h.mu.Unlock()
	if rt == nil {
		partnerFail(w, http.StatusNotFound, "routine not found")
		return
	}
	sid, err := s.partnerFireRoutine(cp)
	if err != nil {
		partnerFail(w, http.StatusConflict, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "session_id": sid})
}

func (s *Server) handlePartnerGoalPut(w http.ResponseWriter, r *http.Request) {
	var body struct {
		SessionID    string `json:"session_id"`
		Autopilot    *bool  `json:"autopilot"`
		EveryMinutes int    `json:"every_minutes"`
	}
	if err := decodePartnerBody(r, &body); err != nil {
		partnerFail(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	g, ok := s.findLifeGoal(r.PathValue("goal_id"))
	if !ok {
		partnerFail(w, http.StatusNotFound, "goal not found")
		return
	}
	var link partnerGoalLink
	if sid := strings.TrimSpace(body.SessionID); sid != "" {
		if _, ok, _ := s.sessions.Get(sid); !ok {
			partnerFail(w, http.StatusNotFound, "session not found")
			return
		}
		link = s.partnerBindGoal(g.ID, sid)
	}
	if body.Autopilot != nil || body.EveryMinutes > 0 {
		on := link.Autopilot
		s.partner.mu.Lock()
		if l := s.partner.st.Goals[g.ID]; l != nil {
			on = l.Autopilot
		}
		s.partner.mu.Unlock()
		if body.Autopilot != nil {
			on = *body.Autopilot
		}
		var err error
		link, err = s.partnerSetAutopilot(g.ID, on, body.EveryMinutes)
		if err != nil {
			partnerFail(w, http.StatusBadRequest, err.Error())
			return
		}
	}
	if link.GoalID == "" {
		var err error
		link, err = s.partnerEnsureGoalSession(g.ID)
		if err != nil {
			partnerFail(w, http.StatusInternalServerError, err.Error())
			return
		}
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "link": link})
}

// handlePartnerGoalStep asks Remedy for one step on a goal now.
func (s *Server) handlePartnerGoalStep(w http.ResponseWriter, r *http.Request) {
	g, ok := s.findLifeGoal(r.PathValue("goal_id"))
	if !ok {
		partnerFail(w, http.StatusNotFound, "goal not found")
		return
	}
	link, err := s.partnerEnsureGoalSession(g.ID)
	if err != nil {
		partnerFail(w, http.StatusInternalServerError, err.Error())
		return
	}
	queued, reason := s.partnerQueueGoalRun(g, link.SessionID)
	if !queued {
		partnerFail(w, http.StatusConflict, reason)
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "session_id": link.SessionID, "capacity_note": s.partnerCapacity().Sentence()})
}

func (s *Server) handlePartnerChildSpawn(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Label  string `json:"label"`
		Task   string `json:"task"`
		GoalID string `json:"goal_id"`
	}
	if err := decodePartnerBody(r, &body); err != nil {
		partnerFail(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	child, err := s.partnerSpawnChild(childSpec{Label: body.Label, Task: body.Task, GoalID: body.GoalID, Origin: "desktop"})
	if err != nil {
		partnerFail(w, http.StatusBadRequest, err.Error())
		return
	}
	row := childPublic(child)
	row["session_id"] = child.SessionID
	writeJSON(w, http.StatusOK, map[string]any{"ok": true, "child": row, "capacity_note": s.partnerCapacity().Sentence()})
}

func (s *Server) handlePartnerChildMessage(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Text string `json:"text"`
	}
	if err := decodePartnerBody(r, &body); err != nil {
		partnerFail(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	if err := s.partnerMessageChild(r.PathValue("id"), body.Text, "desktop"); err != nil {
		partnerFail(w, http.StatusBadRequest, err.Error())
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handlePartnerChildStop(w http.ResponseWriter, r *http.Request) {
	if !s.partnerStopChild(r.PathValue("id")) {
		partnerFail(w, http.StatusNotFound, "child not found")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

func (s *Server) handlePartnerChildForget(w http.ResponseWriter, r *http.Request) {
	if !s.partnerForgetChild(r.PathValue("id")) {
		partnerFail(w, http.StatusConflict, "child not found or still working")
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}

// handlePartnerChannelPut picks (or clears) the chat Remedy sends updates
// to. Only a chat that has actually messaged Remedy can be chosen.
func (s *Server) handlePartnerChannelPut(w http.ResponseWriter, r *http.Request) {
	var body struct {
		Channel string `json:"channel"`
		ChatID  string `json:"chat_id"`
	}
	if err := decodePartnerBody(r, &body); err != nil {
		partnerFail(w, http.StatusBadRequest, "invalid JSON body")
		return
	}
	h := s.partner
	h.mu.Lock()
	defer h.mu.Unlock()
	ch := strings.ToLower(strings.TrimSpace(body.Channel))
	chat := strings.TrimSpace(body.ChatID)
	if ch == "" && chat == "" {
		h.st.OwnerChannel = nil
		h.saveLocked()
		writeJSON(w, http.StatusOK, map[string]any{"ok": true, "owner": nil})
		return
	}
	for _, rc := range h.st.RecentChats {
		if rc.Channel == ch && rc.ChatID == chat {
			cp := rc
			h.st.OwnerChannel = &cp
			h.recordLocked(partnerActivity{Kind: "system", Title: "Updates will also go to " + channelDisplayName(ch), Channel: ch})
			h.saveLocked()
			writeJSON(w, http.StatusOK, map[string]any{"ok": true, "owner": cp})
			return
		}
	}
	writeJSON(w, http.StatusBadRequest, map[string]any{"ok": false, "detail": "message Remedy from that chat first, then pick it here"})
}

func (s *Server) handlePartnerActivitySeen(w http.ResponseWriter, _ *http.Request) {
	h := s.partner
	h.mu.Lock()
	h.st.ActivitySeen = h.nowISO()
	h.saveLocked()
	h.mu.Unlock()
	writeJSON(w, http.StatusOK, map[string]any{"ok": true})
}
