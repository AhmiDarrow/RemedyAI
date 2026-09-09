package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net/http"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/hive"
)

var errHiveHomeMissing = errors.New("hive home not configured")

type hiveSpawnBody struct {
	Goal        string `json:"goal"`
	Cadence     string `json:"cadence"`
	BudgetSteps int    `json:"budget_steps"`
	PulseS      int    `json:"pulse_s"`
}

type hiveIDBody struct {
	HiveID string `json:"hive_id"`
	Goal   string `json:"goal"`
}

func (s *Server) handleHiveRoster(w http.ResponseWriter, _ *http.Request) {
	if s.hiveFS == nil || !s.hiveFS.ready() {
		writeJSON(w, http.StatusOK, map[string]any{
			"daughters":     []any{},
			"live_posts":    0,
			"live_foragers": 0,
			"count":         0,
		})
		return
	}
	all, err := s.hiveFS.listAll()
	if err != nil {
		writeJSON(w, http.StatusInternalServerError, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	rows := make([]map[string]any, 0, len(all))
	livePosts := 0
	liveForagers := 0
	for _, d := range all {
		rows = append(rows, d.rosterLine())
		if d.Cadence == hiveCadencePost && d.Status != hiveStatusRetired && d.Status != hiveStatusCancelled {
			livePosts++
		}
		if d.Cadence == hiveCadenceForager && (d.Status == hiveStatusPending || d.Status == hiveStatusRunning) {
			liveForagers++
		}
	}
	if len(rows) > hiveRosterCap {
		rows = rows[:hiveRosterCap]
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"daughters":     rows,
		"live_posts":    livePosts,
		"live_foragers": liveForagers,
		"count":         len(all),
	})
}

func (s *Server) handleHiveSpawn(w http.ResponseWriter, r *http.Request) {
	var body hiveSpawnBody
	dec := json.NewDecoder(r.Body)
	dec.UseNumber()
	if err := dec.Decode(&body); err != nil && err != io.EOF {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "invalid JSON body"})
		return
	}
	goal := strings.TrimSpace(body.Goal)
	if goal == "" {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "goal is required"})
		return
	}
	if s.hiveFS == nil || !s.hiveFS.ready() {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "hive home not configured"})
		return
	}
	cad := strings.ToLower(strings.TrimSpace(body.Cadence))
	if cad != hiveCadencePost {
		cad = hiveCadenceForager
	}
	if cad == hiveCadencePost {
		posts, err := s.hiveFS.livePosts()
		if err != nil {
			writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		if len(posts) >= hiveDefaultPosts {
			writeJSON(w, http.StatusOK, map[string]any{
				"ok":    false,
				"error": "At capacity (4 standing posts). Retire one first.",
			})
			return
		}
	} else {
		live, err := s.hiveFS.livePulses()
		if err != nil {
			writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
			return
		}
		if len(live) >= hiveDefaultLivePulses {
			writeJSON(w, http.StatusOK, map[string]any{
				"ok":    false,
				"error": "At capacity (8 live foragers). Collect or retire first.",
			})
			return
		}
	}
	budget := body.BudgetSteps
	if budget <= 0 {
		budget = hiveDefaultBudgetSteps
	}
	pulse := 0
	if cad == hiveCadencePost {
		pulse = clampHivePulseS(body.PulseS)
	}
	d, err := s.hiveFS.hire(goal, cad, "", "", "", budget, pulse)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	started := s.superviseHiveDaughter(d)
	if cad == hiveCadencePost {
		d.Status = hiveStatusAsleep
		d.NextPulseAt = ""
		_ = s.hiveFS.save(d)
	} else if started {
		d.Status = hiveStatusRunning
		_ = s.hiveFS.save(d)
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"hive_id": d.ID,
		"cadence": d.Cadence,
		"status":  d.Status,
		"started": started,
		"goal":    d.Goal,
	})
}

func (s *Server) handleHiveAssign(w http.ResponseWriter, r *http.Request) {
	var body hiveIDBody
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(&body); err != nil && err != io.EOF {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "invalid JSON body"})
		return
	}
	hid := strings.TrimSpace(body.HiveID)
	goal := strings.TrimSpace(body.Goal)
	if hid == "" {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "hive_id required"})
		return
	}
	if goal == "" {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "goal required"})
		return
	}
	if s.hiveFS == nil || !s.hiveFS.ready() {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "hive home not configured"})
		return
	}
	d, ok, err := s.hiveFS.get(hid)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if !ok {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "not found"})
		return
	}
	if d.Cadence != hiveCadencePost {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "hive_assign only works on standing posts"})
		return
	}
	if d.Status == hiveStatusRetired || d.Status == hiveStatusCancelled {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "bot is already retired"})
		return
	}
	d.Goal = trimRunes(goal, 800)
	if d.Journal == nil {
		d.Journal = map[string]any{}
	}
	d.Journal["charter"] = d.Goal
	if err := s.hiveFS.save(d); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"hive_id": d.ID,
		"goal":    d.Goal,
		"status":  d.Status,
	})
}

func (s *Server) handleHiveRetire(w http.ResponseWriter, r *http.Request) {
	var body hiveIDBody
	dec := json.NewDecoder(r.Body)
	if err := dec.Decode(&body); err != nil && err != io.EOF {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "invalid JSON body"})
		return
	}
	hid := strings.TrimSpace(body.HiveID)
	if hid == "" {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "hive_id required"})
		return
	}
	if s.hiveFS == nil || !s.hiveFS.ready() {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "hive home not configured"})
		return
	}
	d, ok, err := s.hiveFS.get(hid)
	if err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	if !ok {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": "not found"})
		return
	}
	if s.hiveMgr != nil {
		_ = s.hiveMgr.Cancel(d.ID)
	}
	if s.claims != nil {
		reason := "hive_retire"
		s.claims.Abort(d.SessionID, nil, &reason)
	}
	d.Status = hiveStatusRetired
	if err := s.hiveFS.save(d); err != nil {
		writeJSON(w, http.StatusOK, map[string]any{"ok": false, "error": err.Error()})
		return
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"ok":      true,
		"hive_id": d.ID,
		"status":  d.Status,
	})
}

// superviseHiveDaughter registers a scoped hive.Manager agent.
// Foragers run one cognition pulse via TurnRunner (Go owns ReAct). Standing
// posts wait until cancel / retire (pulse schedule is assign-driven).
func (s *Server) superviseHiveDaughter(d hiveDaughter) bool {
	if s == nil || s.hiveMgr == nil || strings.TrimSpace(d.ID) == "" {
		return false
	}
	err := s.hiveMgr.Spawn(hive.Spec{
		ID:           d.ID,
		Goals:        []string{d.Goal},
		MemoryScope:  "hive:" + d.ID,
		Capabilities: []string{"read", "search"},
		Run: func(ctx context.Context, _ *hive.Agent) error {
			if d.Cadence == hiveCadencePost {
				<-ctx.Done()
				return ctx.Err()
			}
			return s.runHiveForagerPulse(ctx, d)
		},
	})
	return err == nil
}

func hiveForagerCharter(d hiveDaughter) string {
	extra := ""
	if d.Journal != nil {
		if notes, ok := d.Journal["notes"].([]any); ok && len(notes) > 0 {
			var lines []string
			start := 0
			if len(notes) > 4 {
				start = len(notes) - 4
			}
			for _, n := range notes[start:] {
				m, ok := n.(map[string]any)
				if !ok {
					continue
				}
				outcome := strings.TrimSpace(fmt.Sprint(m["outcome"]))
				if outcome == "" || outcome == "<nil>" {
					continue
				}
				lines = append(lines, "- "+outcome)
			}
			if len(lines) > 0 {
				extra = "\n\nJournal of prior pulses:\n" + strings.Join(lines, "\n")
			}
		}
	}
	return "You are a hive daughter of Remedy. You do not speak to the owner. " +
		"Report a compact outcome. Prefer tools over essays.\n\n" +
		"Job: " + d.Goal + extra
}

// runHiveForagerPulse drives one TurnRunner pass and persists the return packet.
func (s *Server) runHiveForagerPulse(ctx context.Context, d hiveDaughter) error {
	if s == nil {
		return errors.New("hive server missing")
	}
	if s.runner == nil {
		s.finishHiveForager(d, hivePacket{
			Goal:     d.Goal,
			Done:     false,
			Outcome:  "pulse failed: turn runner unavailable",
			Blockers: []string{"pulse_failed"},
		}, hiveStatusBlocked)
		return errors.New("hive forager requires turn runner")
	}
	budget := d.BudgetSteps
	if budget < 1 {
		budget = hiveDefaultBudgetSteps
	}
	if budget > hiveMaxBudgetSteps {
		budget = hiveMaxBudgetSteps
	}
	projectPath := effectiveTurnProjectPath(d.ProjectPath)
	var text strings.Builder
	err := s.runner.RunTurn(ctx, TurnRequest{
		Prompt:        hiveForagerCharter(d),
		SessionID:     d.SessionID,
		ProjectPath:   projectPath,
		MaxIterations: budget,
		Origin:        "hive:" + d.ParentSessionID,
	}, func(tok string) error {
		if body, ok := modelTextToken(tok); ok {
			tok = body
		} else if strings.HasPrefix(tok, "@@") {
			return nil
		}
		text.WriteString(tok)
		return nil
	})
	aborted := errors.Is(err, context.Canceled) || errors.Is(err, context.DeadlineExceeded)
	if err != nil && !aborted {
		s.finishHiveForager(d, hivePacket{
			Goal:     d.Goal,
			Done:     false,
			Outcome:  trimRunes("pulse failed: "+err.Error(), 400),
			Blockers: []string{"pulse_failed"},
		}, hiveStatusBlocked)
		return err
	}
	outcome := strings.TrimSpace(text.String())
	pkt := hivePacket{Goal: d.Goal, Outcome: trimRunes(outcome, 800)}
	status := hiveStatusReported
	if aborted {
		pkt.Done = false
		pkt.Blockers = []string{"cancelled"}
		if pkt.Outcome == "" {
			pkt.Outcome = "cancelled"
		}
		status = hiveStatusCancelled
	} else {
		pkt.Done = outcome != ""
		if pkt.Outcome == "" {
			pkt.Outcome = "pulse completed with no text"
		}
	}
	s.finishHiveForager(d, pkt, status)
	return nil
}

func (s *Server) finishHiveForager(d hiveDaughter, pkt hivePacket, status string) {
	if s == nil || s.hiveFS == nil || !s.hiveFS.ready() {
		return
	}
	fresh, ok, err := s.hiveFS.get(d.ID)
	if err != nil || !ok {
		return
	}
	if fresh.Status == hiveStatusRetired || fresh.Status == hiveStatusCancelled {
		return
	}
	fresh.Packet = &pkt
	fresh.Status = status
	if fresh.Journal == nil {
		fresh.Journal = map[string]any{}
	}
	count := 0
	switch v := fresh.Journal["pulse_count"].(type) {
	case float64:
		count = int(v)
	case int:
		count = v
	}
	fresh.Journal["pulse_count"] = count + 1
	_ = s.hiveFS.save(fresh)
}
