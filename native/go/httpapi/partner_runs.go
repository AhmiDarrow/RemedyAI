package httpapi

import (
	"context"
	"errors"
	"log"
	"regexp"
	"strings"
	"time"
)

// Background runs: a turn Remedy takes without the owner typing — a child at
// work, a routine, a goal on autopilot. One dispatcher starts queued runs as
// the governor makes room; each run claims its session like any turn, so the
// owner typing into the same session simply makes the run wait.

// partnerHistoryMessages keeps background turns lean: the session's last few
// exchanges, not the full default window. Background work is narrow by
// design, and every token here is spent without the owner watching.
const partnerHistoryMessages = 6

var partnerStatusLine = regexp.MustCompile(`(?im)^\s*\**\s*STATUS\s*\**\s*:\s*\**\s*(done|working|waiting|blocked)\b\**\s*[—:\-–]*\s*(.*)$`)

// parsePartnerStatus pulls the trailing "STATUS: <state> — <why>" line a
// background turn is asked to end with. The last such line wins. A reply
// without one is "working" with no explanation.
func parsePartnerStatus(reply string) (state, why string) {
	matches := partnerStatusLine.FindAllStringSubmatch(reply, -1)
	if len(matches) == 0 {
		return partnerStateWorking, ""
	}
	last := matches[len(matches)-1]
	return strings.ToLower(last[1]), strings.TrimSpace(strings.Trim(last[2], "*_ "))
}

// stripPartnerStatus removes the machine status line from what the owner reads.
func stripPartnerStatus(reply string) string {
	return strings.TrimSpace(partnerStatusLine.ReplaceAllString(reply, ""))
}

// summarizeReply is a short plain line for activity cards: the stated reason
// if there is one, else the first sentence-ish of the reply.
func summarizeReply(reply, why string) string {
	if why = strings.TrimSpace(why); why != "" {
		return truncateRunes(why, 220)
	}
	body := stripPartnerStatus(reply)
	body = strings.TrimSpace(strings.TrimLeft(body, "#*-> "))
	if i := strings.IndexAny(body, "\n"); i > 0 {
		body = body[:i]
	}
	return truncateRunes(body, 220)
}

func (s *Server) startPartnerDispatcher(ctx context.Context) {
	h := s.partner
	if h == nil {
		return
	}
	loopCtx, cancel := context.WithCancel(ctx)
	h.mu.Lock()
	h.cancel = cancel
	h.mu.Unlock()
	h.wg.Add(1)
	go func() {
		defer h.wg.Done()
		tick := time.NewTicker(20 * time.Second)
		defer tick.Stop()
		for {
			select {
			case <-loopCtx.Done():
				return
			case <-h.wake:
			case <-tick.C:
				s.partnerTick(loopCtx)
			}
			s.partnerDispatch(loopCtx)
		}
	}()
}

func (s *Server) stopPartnerDispatcher() {
	h := s.partner
	if h == nil {
		return
	}
	h.mu.Lock()
	cancel := h.cancel
	h.cancel = nil
	for _, r := range h.running {
		if r.Cancel != nil {
			r.Cancel()
		}
	}
	h.mu.Unlock()
	if cancel != nil {
		cancel()
	}
	h.wg.Wait()
}

// partnerDispatch starts as many queued runs as the governor allows. A run
// whose session is busy (the owner is typing there) stays queued.
func (s *Server) partnerDispatch(ctx context.Context) {
	h := s.partner
	if h == nil || s.runner == nil {
		return
	}
	for {
		capa := s.partnerCapacity()
		if capa.Free() <= 0 {
			return
		}
		h.mu.Lock()
		idx := -1
		for i, q := range h.queue {
			if _, running := h.running[q.SessionID]; running {
				continue
			}
			if s.claims != nil && s.claims.IsClaimed(q.SessionID) {
				continue
			}
			idx = i
			break
		}
		if idx < 0 {
			h.mu.Unlock()
			return
		}
		req := h.queue[idx]
		h.queue = append(h.queue[:idx], h.queue[idx+1:]...)
		epoch, claimCtx, ok := s.claims.TryClaim(req.SessionID)
		if !ok {
			// Lost a race with the owner; try again next tick.
			h.queue = append(h.queue, req)
			h.mu.Unlock()
			return
		}
		runCtx, cancel := context.WithTimeout(claimCtx, partnerRunTimeout)
		h.running[req.SessionID] = &partnerRunning{Req: req, StartedAt: h.now(), Cancel: cancel}
		s.markPartnerStateLocked(req, partnerStateWorking, "")
		h.saveLocked()
		h.mu.Unlock()

		// runDetachedStream owns BeginTurn / EndTurn / Release for the claim,
		// exactly as for an owner turn.
		h.wg.Add(1)
		go func(req partnerRunRequest, epoch int, runCtx context.Context, cancel context.CancelFunc) {
			defer h.wg.Done()
			defer cancel()
			s.executePartnerRun(runCtx, epoch, req)
			h.mu.Lock()
			delete(h.running, req.SessionID)
			h.mu.Unlock()
			h.poke()
		}(req, epoch, runCtx, cancel)
		if ctx.Err() != nil {
			return
		}
	}
}

// markPartnerStateLocked updates the child / goal / routine this run belongs to.
func (s *Server) markPartnerStateLocked(req partnerRunRequest, state, update string) {
	h := s.partner
	now := h.nowISO()
	switch req.Kind {
	case "child":
		if c := h.st.Children[req.SessionID]; c != nil {
			c.State = state
			c.UpdatedAt = now
			if update != "" {
				c.LastUpdate = update
			}
		}
	case "autopilot":
		if g := h.st.Goals[req.GoalID]; g != nil {
			g.State = state
			if update != "" {
				g.LastUpdate = update
			}
		}
	case "routine":
		if _, r := h.findRoutineLocked(req.RoutineID); r != nil {
			r.LastState = state
			if update != "" {
				r.LastSummary = update
			}
		}
	}
}

// executePartnerRun runs one background turn through the same detached
// stream path as an owner turn: drafts land while it works, the turn log
// keeps the evidence, and any window can attach and watch it live. Nobody
// reads the frame sink here; it is drained so nothing backs up.
func (s *Server) executePartnerRun(ctx context.Context, epoch int, req partnerRunRequest) {
	h := s.partner
	sess, ok, err := s.sessions.Get(req.SessionID)
	if err != nil || !ok {
		s.claims.Release(req.SessionID, &epoch)
		h.mu.Lock()
		s.markPartnerStateLocked(req, partnerStateError, "Its conversation is gone.")
		h.saveLocked()
		h.mu.Unlock()
		return
	}
	history := s.partnerHistory(req.SessionID)
	shown := strings.TrimSpace(req.Shown)
	if shown == "" {
		shown = req.Prompt
	}
	userMsg, err := s.sessions.AddMessage(sess.ID, "user", shown, nil, nil)
	if err != nil {
		s.claims.Release(req.SessionID, &epoch)
		return
	}
	s.partnerAnnounce(sess.ID, "user")
	projectPath := ""
	if sess.ProjectPath != nil {
		projectPath = effectiveTurnProjectPath(*sess.ProjectPath)
	}
	requestID := newSessionID()
	sink := newFrameSink(requestID, streamQueueSize)
	// The claim context is cancelled on every release, so "was this run
	// stopped?" is read from the stream's own aborted signal instead.
	aborted := false
	drained := make(chan struct{})
	go func() {
		defer close(drained)
		for f := range sink.ch {
			if strings.HasPrefix(f.Data, "event: aborted") {
				aborted = true
			}
		}
	}()
	s.runDetachedStream(sess.ID, epoch, ctx, requestID, TurnRequest{
		SessionID:     req.SessionID,
		Prompt:        req.Prompt,
		Model:         sess.Model,
		Provider:      sess.LLMProvider,
		ProjectPath:   projectPath,
		Origin:        req.Origin,
		PlanMode:      req.Mode == "plan",
		ChatMode:      req.Mode == "chat",
		History:       history,
		UserMessageID: userMsg.ID,
		DrainNudges:   func() []string { return s.claims.DrainNudges(req.SessionID) },
	}, sink)
	<-drained
	s.partnerAnnounce(sess.ID, "assistant")

	text := s.partnerReplyAfter(sess.ID, userMsg.ID)
	state, why := parsePartnerStatus(text)
	turnOK := true
	switch {
	case errors.Is(ctx.Err(), context.DeadlineExceeded):
		// The deadline is the first cause when it fires; a normal release
		// afterwards does not change ctx.Err().
		state, why, turnOK = partnerStateError, "Ran out of time.", false
	case aborted:
		state, why, turnOK = partnerStateStopped, "Stopped.", false
	case strings.TrimSpace(text) == "":
		state, why, turnOK = partnerStateError, "Finished without a report.", false
		log.Printf("partner run %s (%s): no reply", req.Kind, req.SessionID)
	}
	if turnOK && req.Kind == "child" && state == partnerStateWorking {
		// A child that ends a turn without saying otherwise has reported.
		state = partnerStateDone
	}
	// "Still working" is a report, not a live state: once the run has ended
	// the goal rests until its next step, and a routine run is simply done.
	// The overview shows "working" only while a run is actually live.
	if state == partnerStateWorking && turnOK {
		switch req.Kind {
		case "autopilot":
			state = partnerStateIdle
		case "routine":
			state = partnerStateDone
		}
	}
	summary := summarizeReply(text, why)
	s.partnerAfterRun(req, sess, state, summary, stripPartnerStatus(text))
}

// partnerReplyAfter is the assistant text written after the run's prompt.
func (s *Server) partnerReplyAfter(sessionID, userMsgID string) string {
	msgs, err := s.sessions.ListMessages(sessionID, 20, 0)
	if err != nil {
		return ""
	}
	seen := false
	var out []string
	for _, m := range msgs {
		if m.ID == userMsgID {
			seen = true
			out = out[:0]
			continue
		}
		if seen && m.Role == "assistant" {
			if c, _ := m.Content.(string); strings.TrimSpace(c) != "" {
				out = append(out, c)
			}
		}
	}
	return strings.TrimSpace(strings.Join(out, "\n\n"))
}

// partnerAnnounce tells open windows a session changed.
func (s *Server) partnerAnnounce(sessionID, role string) {
	fresh, ok, _ := s.sessions.Get(sessionID)
	if !ok {
		return
	}
	title := fresh.Title
	count := fresh.MessageCount
	oc := "grove"
	if fresh.OriginChannel != nil && strings.TrimSpace(*fresh.OriginChannel) != "" {
		oc = *fresh.OriginChannel
	}
	s.publishSessionEvent(SessionEvent{
		Type:          "message_added",
		SessionID:     sessionID,
		OriginChannel: &oc,
		Title:         &title,
		MessageCount:  &count,
		Role:          &role,
	})
}

// partnerHistory is the trimmed history for a background turn.
func (s *Server) partnerHistory(sid string) []map[string]any {
	full := s.turnHistory(sid)
	if len(full) > partnerHistoryMessages {
		return full[len(full)-partnerHistoryMessages:]
	}
	return full
}

// partnerPersist stores one message and tells open windows about it.
func (s *Server) partnerPersist(sess ChatSession, role, text string, calls, results []map[string]any) {
	if s.sessions == nil {
		return
	}
	var c any = []any{}
	var r any = []any{}
	if len(calls) > 0 {
		c = calls
	}
	if len(results) > 0 {
		r = results
	}
	if _, err := s.sessions.AddMessageFull(sess.ID, role, text, nil, c, r, sess.Model, nil, nil); err != nil {
		return
	}
	fresh, ok, _ := s.sessions.Get(sess.ID)
	if !ok {
		return
	}
	title := fresh.Title
	count := fresh.MessageCount
	rl := role
	oc := "grove"
	if fresh.OriginChannel != nil && strings.TrimSpace(*fresh.OriginChannel) != "" {
		oc = *fresh.OriginChannel
	}
	s.publishSessionEvent(SessionEvent{
		Type:          "message_added",
		SessionID:     sess.ID,
		OriginChannel: &oc,
		Title:         &title,
		MessageCount:  &count,
		Role:          &rl,
	})
}

// partnerAfterRun files the outcome: the child reports to Remedy, the goal
// link and routine remember where they stand, and Grove's timeline gets one
// plain line. No second model call — the report is the child's own words.
func (s *Server) partnerAfterRun(req partnerRunRequest, sess ChatSession, state, summary, body string) {
	h := s.partner
	msgCount := 0
	if fresh, ok, _ := s.sessions.Get(req.SessionID); ok {
		msgCount = fresh.MessageCount
	}
	h.mu.Lock()
	prevUpdate := ""
	if g := h.st.Goals[req.GoalID]; req.Kind == "autopilot" && g != nil {
		prevUpdate = g.LastUpdate
	}
	s.markPartnerStateLocked(req, state, summary)
	now := h.nowISO()
	var parentID, label string
	switch req.Kind {
	case "child":
		if c := h.st.Children[req.SessionID]; c != nil {
			c.Runs++
			parentID, label = c.ParentSessionID, c.Label
		}
		h.recordLocked(partnerActivity{Kind: "child", Title: childActivityTitle(label, state), Detail: summary, State: state, SessionID: req.SessionID})
	case "autopilot":
		if g := h.st.Goals[req.GoalID]; g != nil {
			g.Runs++
			g.LastRunAt = now
			if summary != "" && summary == prevUpdate {
				g.Stalls++
			} else {
				g.Stalls = 0
			}
			if state == partnerStateWaiting || state == partnerStateBlocked {
				g.OwnerSeenCount = msgCount
			}
			if state == partnerStateDone {
				// Remedy believes it is done; the owner confirms. Autopilot
				// stops so she does not keep "finishing" it.
				g.Autopilot = false
			}
		}
		h.recordLocked(partnerActivity{Kind: "autopilot", Title: goalActivityTitle(req.Label, state), Detail: summary, State: state, SessionID: req.SessionID, GoalID: req.GoalID})
	case "routine":
		if _, r := h.findRoutineLocked(req.RoutineID); r != nil {
			r.Runs++
			r.LastRun = now
		}
		h.recordLocked(partnerActivity{Kind: "routine", Title: req.Label, Detail: summary, State: state, SessionID: req.SessionID, RoutineID: req.RoutineID})
	}
	h.saveLocked()
	h.mu.Unlock()

	if req.Kind == "child" && parentID != "" && body != "" {
		s.partnerReportToParent(parentID, req.SessionID, label, state, body)
	}
	if req.Deliver == "messenger" || req.Deliver == "both" {
		msg := req.Label
		if summary != "" {
			msg += "\n" + summary
		}
		s.partnerNotifyOwnerChannel(msg)
	}
}

// partnerReportToParent drops the child's report into Remedy's conversation
// as her own note, so her next turn — whenever the owner speaks — knows it.
func (s *Server) partnerReportToParent(parentID, childID, label, state, body string) {
	parent, ok, err := s.sessions.Get(parentID)
	if err != nil || !ok {
		return
	}
	head := "🌱 **" + strings.TrimSpace(label) + "** "
	switch state {
	case partnerStateDone:
		head += "reported back:"
	case partnerStateWaiting, partnerStateBlocked:
		head += "needs you:"
	case partnerStateError:
		head += "hit a problem:"
	default:
		head += "checked in:"
	}
	s.partnerPersist(parent, "assistant", head+"\n\n"+truncateRunes(body, 3000), nil, nil)
}

func childActivityTitle(label, state string) string {
	label = strings.TrimSpace(label)
	switch state {
	case partnerStateDone:
		return label + " is done"
	case partnerStateWaiting, partnerStateBlocked:
		return label + " needs you"
	case partnerStateError:
		return label + " hit a problem"
	case partnerStateStopped:
		return label + " was stopped"
	default:
		return label + " made progress"
	}
}

func goalActivityTitle(title, state string) string {
	switch state {
	case partnerStateDone:
		return "Remedy thinks “" + title + "” is done — check it"
	case partnerStateWaiting, partnerStateBlocked:
		return "“" + title + "” is waiting on you"
	case partnerStateError:
		return "“" + title + "” hit a problem"
	default:
		return "Worked on “" + title + "”"
	}
}

// partnerRunConcluded reports a background turn that has filed its STATUS
// report. Only runtime-minted partner origins qualify: an owner's own turn
// that happens to contain "STATUS:" is never cut short.
func partnerRunConcluded(origin, lastText string) bool {
	if !isPartnerOrigin(origin) {
		return false
	}
	return partnerStatusLine.MatchString(lastText)
}
