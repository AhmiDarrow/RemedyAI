package httpapi

import (
	"context"
	"fmt"
	"sort"
	"strings"
	"time"
)

// The partner tick: what Remedy notices on her own. It runs on the PC and
// costs no model usage. Only routines with real work (mode "task") and goals
// on autopilot ever reach the model, and only through the governor's queue.

const (
	partnerAutopilotDefaultMinutes = 120
	partnerAutopilotMinMinutes     = 30
	partnerMaxStallShift           = 3 // 2^3 = 8x the base interval at most
)

func (s *Server) partnerTick(ctx context.Context) {
	h := s.partner
	if h == nil || ctx.Err() != nil {
		return
	}
	now := h.now()
	h.mu.Lock()
	h.lastTick = now
	h.mu.Unlock()
	s.partnerRunDueRoutines(now)
	s.partnerHeartbeatSweep(now, false)
	s.partnerQueueAutopilot(now)
}

// ── routines ────────────────────────────────────────────────────────────

func (s *Server) partnerRunDueRoutines(now time.Time) {
	for _, r := range s.partner.dueRoutines(now) {
		s.partnerFireRoutine(r)
	}
}

// partnerFireRoutine runs one routine now. A reminder is posted directly —
// no model. A task is queued for Remedy (or a fresh child, or a goal).
func (s *Server) partnerFireRoutine(r partnerRoutine) (string, error) {
	h := s.partner
	if r.Mode == "remind" {
		h.mu.Lock()
		if _, live := h.findRoutineLocked(r.ID); live != nil {
			live.Runs++
			live.LastRun = h.nowISO()
			live.LastState = partnerStateDone
			live.LastSummary = truncateRunes(r.Prompt, 220)
		}
		h.recordLocked(partnerActivity{Kind: "routine", Title: "⏰ " + r.Title, Detail: r.Prompt, State: partnerStateDone, RoutineID: r.ID})
		h.saveLocked()
		h.mu.Unlock()
		if r.Deliver == "messenger" || r.Deliver == "both" {
			s.partnerNotifyOwnerChannel("⏰ " + r.Title + "\n" + r.Prompt)
		}
		return "", nil
	}
	sessionID := ""
	target := r.Target
	switch {
	case target == "child":
		child, err := s.partnerSpawnChild(childSpec{
			Label: r.Title, Task: r.Prompt, Origin: "partner:routine:" + r.ID, RoutineID: r.ID, Deliver: r.Deliver,
		})
		if err != nil {
			h.record(partnerActivity{Kind: "routine", Title: r.Title, Detail: "Couldn't start: " + err.Error(), State: partnerStateError, RoutineID: r.ID})
			return "", err
		}
		return child.SessionID, nil
	case strings.HasPrefix(target, "goal:"):
		link, err := s.partnerEnsureGoalSession(strings.TrimPrefix(target, "goal:"))
		if err != nil {
			h.record(partnerActivity{Kind: "routine", Title: r.Title, Detail: "Couldn't run: " + err.Error(), State: partnerStateError, RoutineID: r.ID})
			return "", err
		}
		sessionID = link.SessionID
	default:
		sid, err := s.partnerEnsureHeadSession()
		if err != nil {
			return "", err
		}
		sessionID = sid
	}
	prompt := "⏰ Routine “" + r.Title + "” (" + describeSchedule(r.Schedule) + ")\n\n" + r.Prompt +
		"\n\n" + partnerFrugalRule +
		"\nEnd with one line: STATUS: done|waiting|blocked — <one short sentence for the owner>."
	ok, _, reason := h.enqueue(partnerRunRequest{
		Key:       "routine:" + r.ID,
		Kind:      "routine",
		SessionID: sessionID,
		Prompt:    prompt,
		Shown:     "⏰ " + r.Title + " — " + truncateRunes(r.Prompt, 300),
		Origin:    "partner:routine:" + r.ID,
		Label:     r.Title,
		RoutineID: r.ID,
		Deliver:   r.Deliver,
	})
	if !ok {
		h.record(partnerActivity{Kind: "routine", Title: r.Title, Detail: "Skipped: " + reason, State: partnerStateBlocked, RoutineID: r.ID})
		return sessionID, fmt.Errorf("%s", reason)
	}
	h.mu.Lock()
	if _, live := h.findRoutineLocked(r.ID); live != nil {
		live.LastState = partnerStateQueued
	}
	h.saveLocked()
	h.mu.Unlock()
	return sessionID, nil
}

// partnerFrugalRule is appended to every background prompt.
const partnerFrugalRule = "Work frugally: let the PC do what it can (files, shell, scripts, scheduled routines) " +
	"instead of reasoning it out; read only what you need; keep the reply short."

// ── heartbeat: a local sweep, no model ──────────────────────────────────

// partnerSweep is what the heartbeat found, all read from local state.
type partnerSweep struct {
	Approvals     int
	NeedsYou      []string // children / goals waiting on the owner
	Finished      []string // children done since the last sweep
	Overdue       []string // goals whose next step date passed
	FailedRoutine []string
}

func (sw partnerSweep) empty() bool {
	return sw.Approvals == 0 && len(sw.NeedsYou) == 0 && len(sw.Finished) == 0 &&
		len(sw.Overdue) == 0 && len(sw.FailedRoutine) == 0
}

func (sw partnerSweep) digest() string {
	parts := []string{fmt.Sprint(sw.Approvals)}
	for _, l := range [][]string{sw.NeedsYou, sw.Finished, sw.Overdue, sw.FailedRoutine} {
		c := append([]string(nil), l...)
		sort.Strings(c)
		parts = append(parts, strings.Join(c, "|"))
	}
	return strings.Join(parts, "#")
}

// sentence is the plain note Grove and the owner's phone receive.
func (sw partnerSweep) sentence() string {
	bits := []string{}
	if sw.Approvals == 1 {
		bits = append(bits, "1 decision is waiting for you")
	} else if sw.Approvals > 1 {
		bits = append(bits, fmt.Sprintf("%d decisions are waiting for you", sw.Approvals))
	}
	if len(sw.NeedsYou) > 0 {
		bits = append(bits, "waiting on you: "+joinNames(sw.NeedsYou))
	}
	if len(sw.Finished) > 0 {
		bits = append(bits, "finished: "+joinNames(sw.Finished))
	}
	if len(sw.Overdue) > 0 {
		bits = append(bits, "past their next-step date: "+joinNames(sw.Overdue))
	}
	if len(sw.FailedRoutine) > 0 {
		bits = append(bits, "routines that hit a problem: "+joinNames(sw.FailedRoutine))
	}
	if len(bits) == 0 {
		return "All quiet — nothing needs you."
	}
	out := strings.Join(bits, "; ")
	return strings.ToUpper(out[:1]) + out[1:] + "."
}

func joinNames(names []string) string {
	if len(names) > 4 {
		return strings.Join(names[:4], ", ") + fmt.Sprintf(" and %d more", len(names)-4)
	}
	return strings.Join(names, ", ")
}

func (s *Server) partnerCollectSweep(since string) partnerSweep {
	var sw partnerSweep
	if s.approvals != nil {
		sw.Approvals = len(s.approvals.ListPending(""))
	}
	h := s.partner
	h.mu.Lock()
	for _, c := range h.sortedChildrenLocked() {
		switch c.State {
		case partnerStateWaiting, partnerStateBlocked:
			sw.NeedsYou = append(sw.NeedsYou, c.Label)
		case partnerStateDone:
			if since == "" || c.UpdatedAt > since {
				sw.Finished = append(sw.Finished, c.Label)
			}
		}
	}
	waitingGoals := map[string]bool{}
	for id, g := range h.st.Goals {
		if g.State == partnerStateWaiting || g.State == partnerStateBlocked {
			waitingGoals[id] = true
		}
	}
	for _, r := range h.st.Routines {
		if r.Enabled && r.LastState == partnerStateError {
			sw.FailedRoutine = append(sw.FailedRoutine, r.Title)
		}
	}
	h.mu.Unlock()

	today := time.Now().Format("2006-01-02")
	lifeGoalMu.Lock()
	f := s.loadLifeGoalsFile()
	lifeGoalMu.Unlock()
	for _, g := range f.Goals {
		if g.Status != "open" && g.Status != "active" {
			continue
		}
		if waitingGoals[g.ID] {
			sw.NeedsYou = append(sw.NeedsYou, g.Title)
		}
		if by := strings.TrimSpace(g.NextBy); len(by) >= 10 && by[:10] < today {
			sw.Overdue = append(sw.Overdue, g.Title)
		}
	}
	return sw
}

// partnerHeartbeatSweep looks around and, if anything changed since the
// last note, writes one plain line to Grove (and the owner's chat when they
// chose one). force runs it now regardless of interval and quiet hours.
func (s *Server) partnerHeartbeatSweep(now time.Time, force bool) (partnerSweep, bool) {
	h := s.partner
	h.mu.Lock()
	hb := h.st.Heartbeat
	h.mu.Unlock()
	if !force {
		if !hb.Enabled {
			return partnerSweep{}, false
		}
		if last, err := time.Parse(time.RFC3339, hb.LastAt); err == nil {
			every := hb.EveryMinutes
			if every < partnerMinEveryMinutes {
				every = partnerMinEveryMinutes
			}
			if now.Sub(last) < time.Duration(every)*time.Minute {
				return partnerSweep{}, false
			}
		}
		if inQuietHours(now.In(time.Local), hb.QuietStart, hb.QuietEnd) {
			return partnerSweep{}, false
		}
	}
	sw := s.partnerCollectSweep(hb.LastAt)
	digest := sw.digest()
	note := sw.sentence()
	changed := !sw.empty() && digest != hb.LastDigest
	h.mu.Lock()
	h.st.Heartbeat.LastAt = now.UTC().Format(time.RFC3339)
	h.st.Heartbeat.LastDigest = digest
	if changed || force {
		h.st.Heartbeat.LastNote = note
	}
	if changed {
		h.recordLocked(partnerActivity{Kind: "heartbeat", Title: "Remedy checked in", Detail: note})
	}
	h.saveLocked()
	h.mu.Unlock()
	if changed && s.partnerOwnerAway() {
		s.partnerNotifyOwnerChannel("Remedy checked in: " + note)
	}
	return sw, changed
}

func (s *Server) partnerOwnerAway() bool {
	st := s.hearthLoad()
	return st.Locus == hearthLocusAway
}

// ── goal autopilot ──────────────────────────────────────────────────────

// partnerAutopilotDue decides whether a goal should get another run. Pure
// so the backoff and wait-for-owner rules are tested directly.
func partnerAutopilotDue(link partnerGoalLink, now time.Time, sessionMessages int) bool {
	if !link.Autopilot || link.SessionID == "" {
		return false
	}
	switch link.State {
	case partnerStateQueued:
		return false
	case partnerStateWaiting, partnerStateBlocked:
		// Do not ask again until the owner has said something there.
		if sessionMessages <= link.OwnerSeenCount {
			return false
		}
	}
	base := link.EveryMinutes
	if base <= 0 {
		base = partnerAutopilotDefaultMinutes
	}
	if base < partnerAutopilotMinMinutes {
		base = partnerAutopilotMinMinutes
	}
	shift := link.Stalls
	if shift > partnerMaxStallShift {
		shift = partnerMaxStallShift
	}
	wait := time.Duration(base<<shift) * time.Minute
	last, err := time.Parse(time.RFC3339, link.LastRunAt)
	if err != nil {
		return true
	}
	return now.Sub(last) >= wait
}

func (s *Server) partnerQueueAutopilot(now time.Time) {
	h := s.partner
	h.mu.Lock()
	hb := h.st.Heartbeat
	links := make([]partnerGoalLink, 0, len(h.st.Goals))
	for _, g := range h.st.Goals {
		if g.Autopilot {
			links = append(links, *g)
		}
	}
	h.mu.Unlock()
	if len(links) == 0 {
		return
	}
	// Autopilot keeps the owner's quiet hours, like the heartbeat.
	if inQuietHours(now.In(time.Local), hb.QuietStart, hb.QuietEnd) {
		return
	}
	goals := map[string]lifeGoal{}
	lifeGoalMu.Lock()
	for _, g := range s.loadLifeGoalsFile().Goals {
		goals[g.ID] = g
	}
	lifeGoalMu.Unlock()
	for _, link := range links {
		g, ok := goals[link.GoalID]
		if !ok || (g.Status != "open" && g.Status != "active") {
			continue
		}
		count := 0
		if sess, ok, _ := s.sessions.Get(link.SessionID); ok {
			count = sess.MessageCount
		} else {
			continue
		}
		if !partnerAutopilotDue(link, now, count) {
			continue
		}
		s.partnerQueueGoalRun(g, link.SessionID)
	}
}

func (s *Server) partnerQueueGoalRun(g lifeGoal, sessionID string) (bool, string) {
	h := s.partner
	var b strings.Builder
	b.WriteString("🌿 Autopilot step for the goal “" + g.Title + "”.\n")
	if g.Why != "" {
		b.WriteString("Why it matters: " + g.Why + "\n")
	}
	if g.DoneLooksLike != "" {
		b.WriteString("Done looks like: " + g.DoneLooksLike + "\n")
	}
	if g.NextAction != "" {
		b.WriteString("Planned next step: " + g.NextAction + "\n")
	}
	b.WriteString("\nTake the next real step toward it — one step, then stop. " +
		"Anything involving money, passwords, sending, submitting or deleting waits for the owner. " +
		partnerFrugalRule +
		"\nEnd with one line: STATUS: working|waiting|blocked|done — <one short sentence for the owner>.")
	ok, _, reason := h.enqueue(partnerRunRequest{
		Key:       "goal:" + g.ID,
		Kind:      "autopilot",
		SessionID: sessionID,
		Prompt:    b.String(),
		Shown:     "🌿 Keep going on “" + g.Title + "”",
		Origin:    "partner:goal:" + g.ID,
		Label:     g.Title,
		GoalID:    g.ID,
	})
	if ok {
		h.mu.Lock()
		if link := h.st.Goals[g.ID]; link != nil {
			link.State = partnerStateQueued
		}
		h.saveLocked()
		h.mu.Unlock()
	}
	return ok, reason
}
