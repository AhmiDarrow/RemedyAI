package httpapi

import (
	"errors"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// Remedy's view of the owner's sessions. She is the one who does the work:
// from Grove, on the desktop or a paired phone, the owner asks, and she can
// see what is open in Studio, start a session for a job, carry on one that is
// already there, and stop one. Sessions she starts or takes on report back to
// her like any other helper, so children.list / children.read / children.stop
// keep working on them.

func partnerSessionToolDefs() []partnerToolDef {
	return []partnerToolDef{
		{
			id:   "sessions.list",
			risk: tools.RiskReadOnly,
			desc: "List the owner's sessions, newest first: the title, the project folder, where it lives (studio, grove, a messenger), " +
				"how many messages it has, when it last changed, and whether it is working right now. Use it to see what is already " +
				"open before starting something new, and to find a session the owner refers to.",
			schema: `{"type":"object","properties":{"last":{"type":"integer","minimum":1,"maximum":50,"description":"How many to return (default 20)."},` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolSessionsList,
		},
		{
			id:     "sessions.read",
			risk:   tools.RiskReadOnly,
			desc:   "Read the last messages of any session of the owner's, with the tools each reply ran. target is the session id from sessions.list.",
			schema: `{"type":"object","required":["target"],"properties":{"target":{"type":"string"},"last":{"type":"integer","minimum":1,"maximum":20},` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolSessionsRead,
		},
		{
			id:   "sessions.start",
			risk: tools.RiskMutation,
			desc: "Start a session in Studio for one job and set it working in the background. The owner sees it in Studio's list and can " +
				"open it or take over at any time; it reports back into your conversation when it finishes. mode is build (default: it edits " +
				"and runs things), plan (it writes a plan and changes nothing) or chat. Give project_path when the job belongs to a folder. " +
				"Returns immediately. Check children.list for room first: sessions you start share the PC with your other helpers.",
			schema: `{"type":"object","required":["task"],"properties":{
				"title":{"type":"string","maxLength":60,"description":"Short name the owner sees in Studio."},
				"task":{"type":"string","minLength":1,"description":"The whole job, self-contained: the session does not see this conversation."},
				"project_path":{"type":"string","description":"Absolute path of the folder the job belongs to."},
				"mode":{"type":"string","enum":["build","plan","chat"]},
				"goal_id":{"type":"string","description":"Optional goal this job serves."},
				` + partnerBoundProps + `},"additionalProperties":false}`,
			run: toolSessionsStart,
		},
		{
			id:   "sessions.continue",
			risk: tools.RiskMutation,
			desc: "Carry on an existing session with a follow-up: an answer, a correction, the next step, or simply 'keep going'. " +
				"It works on a session you started and on one the owner started in Studio; from then on it reports back to you. " +
				"It will not interrupt a session that is working right now.",
			schema: `{"type":"object","required":["target","text"],"properties":{
				"target":{"type":"string","description":"The session id from sessions.list."},
				"text":{"type":"string","minLength":1},
				` + partnerBoundProps + `},"additionalProperties":false}`,
			run: toolSessionsContinue,
		},
		{
			id:     "sessions.stop",
			risk:   tools.RiskMutation,
			desc:   "Stop a session that is working right now. What it already did stays; nothing is undone.",
			schema: `{"type":"object","required":["target"],"properties":{"target":{"type":"string"},` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolSessionsStop,
		},
	}
}

// sessionWhere names the surface a session belongs to.
func sessionWhere(s *Server, sess ChatSession) string {
	if s.partner != nil {
		s.partner.mu.Lock()
		head := s.partner.st.HeadSessionID
		_, child := s.partner.st.Children[sess.ID]
		s.partner.mu.Unlock()
		if sess.ID == head {
			return "grove (your own conversation)"
		}
		if child && sess.OriginChannel != nil && *sess.OriginChannel == "grove" {
			return "grove (your helper)"
		}
		if child {
			return "studio (you are running it)"
		}
	}
	if sess.OriginChannel == nil || strings.TrimSpace(*sess.OriginChannel) == "" {
		return "studio"
	}
	return *sess.OriginChannel
}

func sessionPublic(s *Server, sess ChatSession) map[string]any {
	row := map[string]any{
		"session_id": sess.ID,
		"title":      sess.Title,
		"where":      sessionWhere(s, sess),
		"messages":   sess.MessageCount,
		"working":    sess.Claimed,
	}
	if sess.ProjectPath != nil && strings.TrimSpace(*sess.ProjectPath) != "" {
		row["project_path"] = *sess.ProjectPath
	}
	if sess.UpdatedAt != nil {
		row["updated_at"] = *sess.UpdatedAt
	}
	return row
}

func toolSessionsList(s *Server, in partnerToolInput) (any, error) {
	if s.sessions == nil {
		return nil, errors.New("sessions are not available")
	}
	n := in.Last
	if n <= 0 || n > 50 {
		n = 20
	}
	list, err := s.sessions.List(n, 0)
	if err != nil {
		return nil, err
	}
	rows := make([]map[string]any, 0, len(list))
	for _, sess := range list {
		// The store does not know which sessions hold a live turn.
		sess.Claimed = s.claims != nil && s.claims.ActiveRequest(sess.ID) != ""
		rows = append(rows, sessionPublic(s, sess))
	}
	return map[string]any{"sessions": rows, "total": len(rows)}, nil
}

// findOwnerSession resolves a tool's target to a session, or explains why not.
func findOwnerSession(s *Server, target string) (ChatSession, error) {
	if s.sessions == nil {
		return ChatSession{}, errors.New("sessions are not available")
	}
	id := strings.TrimSpace(target)
	if id == "" {
		return ChatSession{}, errors.New("say which session: target is its id from sessions.list")
	}
	sess, ok, err := s.sessions.Get(id)
	if err != nil {
		return ChatSession{}, err
	}
	if !ok {
		return ChatSession{}, errors.New("no session with that id — check sessions.list")
	}
	sess.Claimed = s.claims != nil && s.claims.ActiveRequest(sess.ID) != ""
	return sess, nil
}

func toolSessionsRead(s *Server, in partnerToolInput) (any, error) {
	sess, err := findOwnerSession(s, in.Target)
	if err != nil {
		return nil, err
	}
	n := in.Last
	if n <= 0 || n > 20 {
		n = 6
	}
	msgs, err := s.sessions.ListMessages(sess.ID, n, 0)
	if err != nil {
		return nil, err
	}
	rows := make([]map[string]any, 0, len(msgs))
	for _, m := range msgs {
		content, _ := m.Content.(string)
		content = truncStr(content, 2000)
		if ledger := toolLedgerLine(m.ToolCalls, m.ToolResults); ledger != "" {
			content = strings.TrimSpace(content + "\n" + ledger)
		}
		rows = append(rows, map[string]any{"role": m.Role, "content": content})
	}
	return map[string]any{"session": sessionPublic(s, sess), "messages": rows}, nil
}

func toolSessionsStart(s *Server, in partnerToolInput) (any, error) {
	label := strings.TrimSpace(in.Title)
	if label == "" {
		label = in.Label
	}
	child, err := s.partnerSpawnChild(childSpec{
		Label:           label,
		Task:            in.Task,
		GoalID:          in.GoalID,
		ParentSessionID: in.SessionID,
		ProjectPath:     in.ProjectPath,
		Origin:          in.CallerOrigin,
		InStudio:        true,
		Mode:            in.Mode,
	})
	if err != nil {
		return nil, err
	}
	capa := s.partnerCapacity()
	note := "Started in Studio. It will report back into this conversation when it finishes."
	if capa.Free() == 0 {
		note = "Queued — " + capa.Sentence()
	}
	out := childPublic(child)
	out["session_id"] = child.SessionID
	return map[string]any{"session": out, "note": note}, nil
}

func toolSessionsContinue(s *Server, in partnerToolInput) (any, error) {
	sess, err := findOwnerSession(s, in.Target)
	if err != nil {
		return nil, err
	}
	if isChildOrigin(in.CallerOrigin) {
		return nil, errors.New("a helper cannot direct other sessions — report back to Remedy instead")
	}
	h := s.partner
	h.mu.Lock()
	head := h.st.HeadSessionID
	_, known := h.st.Children[sess.ID]
	h.mu.Unlock()
	if sess.ID == head || sess.ID == strings.TrimSpace(in.SessionID) {
		return nil, errors.New("that is this conversation — just answer here")
	}
	if sess.Claimed {
		return nil, errors.New("that session is working right now — wait for it to finish, or stop it first with sessions.stop")
	}
	if !known {
		// Take the owner's session on: from here it reports to her like a
		// helper she started, and the owner still sees it where it was.
		if sess.OriginChannel != nil && strings.TrimSpace(*sess.OriginChannel) != "" && *sess.OriginChannel != "grove" {
			return nil, errors.New("that conversation belongs to " + *sess.OriginChannel + ", not Studio — it is not one to run work in")
		}
		parent := strings.TrimSpace(in.SessionID)
		if parent == "" {
			parent = head
		}
		now := h.nowISO()
		h.mu.Lock()
		if h.liveChildrenLocked() >= partnerLiveChildrenMax {
			h.mu.Unlock()
			return nil, errors.New("Remedy already has the most sessions she can look after — let some finish or stop them first")
		}
		h.st.Children[sess.ID] = &partnerChild{
			SessionID: sess.ID, Label: sess.Title, Task: truncateRunes(strings.TrimSpace(in.Text), 4000),
			ParentSessionID: parent, State: partnerStateQueued, CreatedAt: now, UpdatedAt: now,
		}
		h.recordLocked(partnerActivity{Kind: "child", Title: "Took on " + sess.Title, Detail: truncateRunes(in.Text, 240), State: partnerStateQueued, SessionID: sess.ID})
		h.saveLocked()
		h.mu.Unlock()
	}
	if err := s.partnerMessageChild(sess.ID, in.Text, in.CallerOrigin); err != nil {
		return nil, err
	}
	return map[string]any{"session": sessionPublic(s, sess), "note": "Sent. It will report back into this conversation when it finishes."}, nil
}

func toolSessionsStop(s *Server, in partnerToolInput) (any, error) {
	sess, err := findOwnerSession(s, in.Target)
	if err != nil {
		return nil, err
	}
	if isChildOrigin(in.CallerOrigin) {
		return nil, errors.New("a helper cannot stop other sessions — report back to Remedy instead")
	}
	if s.partnerStopChild(sess.ID) {
		return map[string]any{"note": "Stopped."}, nil
	}
	if !sess.Claimed || s.claims == nil {
		return map[string]any{"note": "It was not working."}, nil
	}
	reason := "stopped by Remedy for the owner"
	s.claims.Abort(sess.ID, nil, &reason)
	return map[string]any{"note": "Stopped."}, nil
}
