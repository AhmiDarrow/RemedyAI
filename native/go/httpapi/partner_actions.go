package httpapi

import (
	"context"
	"errors"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/gateway"
)

// Actions shared by the HTTP API (owner clicks in Grove) and Remedy's own
// tools: start a child, talk to one, stop one, keep her head conversation,
// bind a goal to its conversation, and reach the owner's phone.

type childSpec struct {
	Label           string
	Task            string
	ParentSessionID string
	GoalID          string
	ProjectPath     string
	Origin          string // origin of the turn that asked for the child
	RoutineID       string
	Deliver         string
}

// partnerEnsureHeadSession returns Remedy's own conversation, creating it
// on first use. The head is where her children report and where routines
// that target "head" run.
func (s *Server) partnerEnsureHeadSession() (string, error) {
	h := s.partner
	if h == nil || s.sessions == nil {
		return "", errors.New("sessions are not available")
	}
	h.mu.Lock()
	sid := h.st.HeadSessionID
	h.mu.Unlock()
	if sid != "" {
		if _, ok, _ := s.sessions.Get(sid); ok {
			return sid, nil
		}
	}
	oc := "grove"
	sess, err := s.sessions.Create(createSessionRequest{Title: "✦ Remedy", OriginChannel: &oc})
	if err != nil {
		return "", err
	}
	title := sess.Title
	s.publishSessionEvent(SessionEvent{Type: "session_created", SessionID: sess.ID, OriginChannel: &oc, Title: &title})
	h.mu.Lock()
	h.st.HeadSessionID = sess.ID
	h.saveLocked()
	h.mu.Unlock()
	return sess.ID, nil
}

// partnerAdoptHeadSession makes an existing conversation Remedy's head
// (Grove migrating its locally remembered home session).
func (s *Server) partnerAdoptHeadSession(sid string) error {
	sid = strings.TrimSpace(sid)
	if _, ok, err := s.sessions.Get(sid); err != nil || !ok {
		return errors.New("session not found")
	}
	h := s.partner
	h.mu.Lock()
	h.st.HeadSessionID = sid
	h.saveLocked()
	h.mu.Unlock()
	return nil
}

func childBrief(spec childSpec, parentLabel string) string {
	var b strings.Builder
	b.WriteString("🌱 You are one of Remedy's children: a focused helper she started for one job. ")
	b.WriteString("Remedy is the head — you report to her, she speaks for you to the owner. ")
	b.WriteString("You cannot start helpers of your own.\n\n")
	b.WriteString("Your job: " + strings.TrimSpace(spec.Task) + "\n\n")
	b.WriteString("Do the job, check that it actually worked, and stop. " +
		"Anything involving money, passwords, sending, submitting or deleting waits for the owner. ")
	b.WriteString(partnerFrugalRule)
	b.WriteString("\nYour reply is your report to " + parentLabel + ": what you did, what you found, what is left. ")
	b.WriteString("End with one line: STATUS: done|waiting|blocked — <one short sentence>.")
	return b.String()
}

// partnerSpawnChild creates a child session with one job and queues its
// first run. It returns at once; the child reports back when it finishes.
func (s *Server) partnerSpawnChild(spec childSpec) (partnerChild, error) {
	h := s.partner
	if h == nil || s.sessions == nil {
		return partnerChild{}, errors.New("sessions are not available")
	}
	if isChildOrigin(spec.Origin) {
		return partnerChild{}, errors.New("a child cannot start children of its own — report back to Remedy instead")
	}
	spec.Task = truncateRunes(strings.TrimSpace(spec.Task), 4000)
	if spec.Task == "" {
		return partnerChild{}, errors.New("give the child a job")
	}
	spec.Label = truncateRunes(strings.TrimSpace(spec.Label), 60)
	if spec.Label == "" {
		spec.Label = truncateRunes(spec.Task, 40)
	}
	h.mu.Lock()
	live := h.liveChildrenLocked()
	h.mu.Unlock()
	if live >= partnerLiveChildrenMax {
		return partnerChild{}, errors.New("Remedy already has the most children she can look after — let some finish or stop them first")
	}
	parent := strings.TrimSpace(spec.ParentSessionID)
	if parent == "" {
		sid, err := s.partnerEnsureHeadSession()
		if err != nil {
			return partnerChild{}, err
		}
		parent = sid
	}
	oc := "grove"
	req := createSessionRequest{Title: "🌱 " + spec.Label, OriginChannel: &oc}
	if p := strings.TrimSpace(spec.ProjectPath); p != "" {
		req.ProjectPath = &p
	} else if ps, ok, _ := s.sessions.Get(parent); ok && ps.ProjectPath != nil {
		// A child works in its parent's folder unless told otherwise.
		req.ProjectPath = ps.ProjectPath
	}
	sess, err := s.sessions.Create(req)
	if err != nil {
		return partnerChild{}, err
	}
	title := sess.Title
	s.publishSessionEvent(SessionEvent{Type: "session_created", SessionID: sess.ID, OriginChannel: &oc, Title: &title})
	now := h.nowISO()
	child := partnerChild{
		SessionID:       sess.ID,
		Label:           spec.Label,
		Task:            spec.Task,
		ParentSessionID: parent,
		GoalID:          strings.TrimSpace(spec.GoalID),
		State:           partnerStateQueued,
		CreatedAt:       now,
		UpdatedAt:       now,
	}
	h.mu.Lock()
	cp := child
	h.st.Children[sess.ID] = &cp
	h.recordLocked(partnerActivity{Kind: "child", Title: "Started " + spec.Label, Detail: truncateRunes(spec.Task, 240), State: partnerStateQueued, SessionID: sess.ID, GoalID: child.GoalID})
	h.saveLocked()
	h.mu.Unlock()

	parentLabel := "Remedy"
	ok, _, reason := h.enqueue(partnerRunRequest{
		Key:       "child:" + sess.ID,
		Kind:      "child",
		SessionID: sess.ID,
		Prompt:    childBrief(spec, parentLabel),
		Shown:     "🌱 Job from Remedy: " + spec.Task,
		Origin:    "partner:child:" + parent,
		Label:     spec.Label,
		GoalID:    child.GoalID,
		RoutineID: spec.RoutineID,
		Deliver:   spec.Deliver,
	})
	if !ok {
		h.mu.Lock()
		if c := h.st.Children[sess.ID]; c != nil {
			c.State = partnerStateBlocked
			c.LastUpdate = reason
		}
		h.saveLocked()
		h.mu.Unlock()
		child.State = partnerStateBlocked
		child.LastUpdate = reason
	}
	return child, nil
}

// partnerMessageChild sends a child a follow-up and queues its next run.
func (s *Server) partnerMessageChild(childID, text, origin string) error {
	h := s.partner
	text = truncateRunes(strings.TrimSpace(text), 4000)
	if text == "" {
		return errors.New("say something to the child")
	}
	h.mu.Lock()
	c := h.st.Children[strings.TrimSpace(childID)]
	var label, parent string
	if c != nil {
		label, parent = c.Label, c.ParentSessionID
		c.State = partnerStateQueued
		c.UpdatedAt = h.nowISO()
	}
	h.saveLocked()
	h.mu.Unlock()
	if c == nil {
		return errors.New("no child with that id — check children.list")
	}
	if strings.TrimSpace(parent) == "" {
		parent = "head"
	}
	ok, _, reason := h.enqueue(partnerRunRequest{
		Key:       "child:" + childID + ":" + shortHexID(6),
		Kind:      "child",
		SessionID: childID,
		Prompt: "Remedy says: " + text + "\n\n" + partnerFrugalRule +
			"\nEnd with one line: STATUS: done|waiting|blocked — <one short sentence>.",
		Shown:  "✦ Remedy: " + text,
		Origin: "partner:child:" + parent,
		Label:  label,
	})
	if !ok {
		return errors.New(reason)
	}
	return nil
}

// partnerStopChild cancels a child's run and drops its queued work.
func (s *Server) partnerStopChild(childID string) bool {
	h := s.partner
	childID = strings.TrimSpace(childID)
	h.mu.Lock()
	c := h.st.Children[childID]
	if c == nil {
		h.mu.Unlock()
		return false
	}
	h.dropQueuedLocked(childID)
	if r := h.running[childID]; r != nil && r.Cancel != nil {
		r.Cancel()
	}
	c.State = partnerStateStopped
	c.UpdatedAt = h.nowISO()
	h.recordLocked(partnerActivity{Kind: "child", Title: c.Label + " was stopped", State: partnerStateStopped, SessionID: childID})
	h.saveLocked()
	h.mu.Unlock()
	return true
}

// partnerForgetChild removes a finished child from the roster. The
// conversation itself stays in the session list.
func (s *Server) partnerForgetChild(childID string) bool {
	h := s.partner
	h.mu.Lock()
	defer h.mu.Unlock()
	if _, ok := h.st.Children[childID]; !ok {
		return false
	}
	if _, running := h.running[childID]; running {
		return false
	}
	h.dropQueuedLocked(childID)
	delete(h.st.Children, childID)
	h.saveLocked()
	return true
}

// partnerEnsureGoalSession binds a goal to a conversation, creating one if
// the goal has none yet.
func (s *Server) partnerEnsureGoalSession(goalID string) (partnerGoalLink, error) {
	goalID = strings.TrimSpace(goalID)
	g, ok := s.findLifeGoal(goalID)
	if !ok {
		return partnerGoalLink{}, errors.New("goal not found")
	}
	h := s.partner
	h.mu.Lock()
	link := h.st.Goals[g.ID]
	if link != nil && link.SessionID != "" {
		cp := *link
		h.mu.Unlock()
		if _, ok, _ := s.sessions.Get(cp.SessionID); ok {
			return cp, nil
		}
		h.mu.Lock()
	}
	h.mu.Unlock()
	oc := "grove"
	sess, err := s.sessions.Create(createSessionRequest{Title: "🌿 " + g.Title, OriginChannel: &oc})
	if err != nil {
		return partnerGoalLink{}, err
	}
	title := sess.Title
	s.publishSessionEvent(SessionEvent{Type: "session_created", SessionID: sess.ID, OriginChannel: &oc, Title: &title})
	return s.partnerBindGoal(g.ID, sess.ID), nil
}

func (s *Server) partnerBindGoal(goalID, sessionID string) partnerGoalLink {
	h := s.partner
	h.mu.Lock()
	defer h.mu.Unlock()
	link := h.st.Goals[goalID]
	if link == nil {
		link = &partnerGoalLink{GoalID: goalID, State: partnerStateIdle}
		h.st.Goals[goalID] = link
	}
	link.SessionID = sessionID
	h.saveLocked()
	return *link
}

func (s *Server) findLifeGoal(id string) (lifeGoal, bool) {
	id = strings.ToLower(strings.TrimSpace(id))
	if id == "" {
		return lifeGoal{}, false
	}
	lifeGoalMu.Lock()
	f := s.loadLifeGoalsFile()
	lifeGoalMu.Unlock()
	for _, g := range f.Goals {
		if strings.ToLower(g.ID) == id {
			return g, true
		}
	}
	for _, g := range f.Goals {
		if strings.Contains(strings.ToLower(g.Title), id) {
			return g, true
		}
	}
	return lifeGoal{}, false
}

// partnerSetAutopilot turns a goal's autopilot on or off.
func (s *Server) partnerSetAutopilot(goalID string, on bool, everyMinutes int) (partnerGoalLink, error) {
	link, err := s.partnerEnsureGoalSession(goalID)
	if err != nil {
		return partnerGoalLink{}, err
	}
	h := s.partner
	h.mu.Lock()
	l := h.st.Goals[link.GoalID]
	l.Autopilot = on
	if everyMinutes > 0 {
		if everyMinutes < partnerAutopilotMinMinutes {
			everyMinutes = partnerAutopilotMinMinutes
		}
		l.EveryMinutes = everyMinutes
	}
	l.Stalls = 0
	if on && (l.State == partnerStateDone || l.State == partnerStateError || l.State == "") {
		l.State = partnerStateIdle
	}
	title := "Autopilot off"
	if on {
		title = "Autopilot on"
	}
	if g, ok := s.findLifeGoal(l.GoalID); ok {
		title += ": " + g.Title
	}
	h.recordLocked(partnerActivity{Kind: "autopilot", Title: title, GoalID: l.GoalID, SessionID: l.SessionID})
	out := *l
	h.saveLocked()
	h.mu.Unlock()
	if on {
		// Turning it on is the owner asking for movement: take the first
		// step now rather than at the next tick.
		if g, ok := s.findLifeGoal(out.GoalID); ok && (g.Status == "open" || g.Status == "active") {
			s.partnerQueueGoalRun(g, out.SessionID)
		}
	}
	return out, nil
}

// ── messengers ──────────────────────────────────────────────────────────

// partnerNoteMessenger records inbound messenger traffic for Grove's
// timeline and remembers the chat so the owner can pick it for updates.
func (s *Server) partnerNoteMessenger(channel, chatID, username, text, sessionID string) {
	h := s.partner
	if h == nil {
		return
	}
	ch := strings.ToLower(strings.TrimSpace(channel))
	who := strings.TrimSpace(username)
	if who == "" {
		who = "someone"
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	now := h.nowISO()
	found := false
	for i := range h.st.RecentChats {
		rc := &h.st.RecentChats[i]
		if rc.Channel == ch && rc.ChatID == chatID {
			rc.LastAt, rc.Username, found = now, truncateRunes(username, 80), true
		}
	}
	if !found {
		h.st.RecentChats = append(h.st.RecentChats, partnerChannel{Channel: ch, ChatID: chatID, Username: truncateRunes(username, 80), LastAt: now})
		if len(h.st.RecentChats) > 8 {
			h.st.RecentChats = h.st.RecentChats[len(h.st.RecentChats)-8:]
		}
	}
	h.recordLocked(partnerActivity{
		Kind:      "messenger",
		Title:     channelDisplayName(ch) + " · " + who,
		Detail:    truncateRunes(text, 240),
		SessionID: sessionID,
		Channel:   ch,
	})
	h.saveLocked()
}

func channelDisplayName(ch string) string {
	switch ch {
	case "telegram":
		return "Telegram"
	case "discord":
		return "Discord"
	case "slack":
		return "Slack"
	case "whatsapp":
		return "WhatsApp"
	case "teams":
		return "Teams"
	case "google_chat":
		return "Google Chat"
	case "signal":
		return "Signal"
	}
	if ch == "" {
		return "Chat"
	}
	return strings.ToUpper(ch[:1]) + ch[1:]
}

// partnerNotifyOwnerChannel sends a short note to the chat the owner chose
// for updates. Nothing is sent when they have not chosen one.
func (s *Server) partnerNotifyOwnerChannel(text string) bool {
	h := s.partner
	if h == nil || s.messengerGW == nil {
		return false
	}
	h.mu.Lock()
	oc := h.st.OwnerChannel
	var target partnerChannel
	if oc != nil {
		target = *oc
	}
	h.mu.Unlock()
	if target.Channel == "" || target.ChatID == "" {
		return false
	}
	ctx, cancel := context.WithTimeout(context.Background(), 20*time.Second)
	defer cancel()
	sent := false
	for _, part := range gateway.SplitMessage(truncateRunes(text, 3500), gateway.ChannelKind(target.Channel)) {
		if _, err := s.messengerGW.SendTo(ctx, gateway.ChannelKind(target.Channel), part, target.ChatID); err == nil {
			sent = true
		}
	}
	return sent
}
