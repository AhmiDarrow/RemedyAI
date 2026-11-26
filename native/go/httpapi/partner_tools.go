package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// Remedy's own tools for running the house: her children, her routines, the
// owner's goals, and a way to reach the owner. session_id and caller_origin
// are bound by the runtime (bindToolInput) — the model never supplies them.

// partnerToolPrefixes name the tools that get the caller binding.
var partnerToolPrefixes = []string{"children.", "routines.", "goals.", "owner.", "grove.", "face.", "sessions."}

func isPartnerTool(name string) bool {
	n := toolNameDotted(strings.TrimSpace(name))
	for _, p := range partnerToolPrefixes {
		if strings.HasPrefix(n, p) {
			return true
		}
	}
	return false
}

const partnerBoundProps = `"session_id":{"type":"string","description":"Bound by the runtime"},
	"caller_origin":{"type":"string","description":"Bound by the runtime"}`

const partnerScheduleSchema = `{
	"type":"object",
	"required":["kind"],
	"properties":{
		"kind":{"type":"string","enum":["every","daily","weekdays","weekly","once"]},
		"every_minutes":{"type":"integer","minimum":15,"description":"For kind=every."},
		"at":{"type":"string","description":"Local time HH:MM for daily / weekdays / weekly."},
		"days":{"type":"array","items":{"type":"string","enum":["mon","tue","wed","thu","fri","sat","sun"]},"description":"For kind=weekly."},
		"once_at":{"type":"string","description":"For kind=once: full date-time with offset (RFC 3339)."}
	},
	"additionalProperties":false
}`

type partnerToolDef struct {
	id     string
	risk   tools.Risk
	desc   string
	schema string
	run    func(s *Server, in partnerToolInput) (any, error)
}

// partnerToolInput is the union of every partner tool's fields.
type partnerToolInput struct {
	SessionID    string           `json:"session_id"`
	CallerOrigin string           `json:"caller_origin"`
	Label        string           `json:"label"`
	Task         string           `json:"task"`
	GoalID       string           `json:"goal_id"`
	ChildID      string           `json:"child_id"`
	Text         string           `json:"text"`
	Last         int              `json:"last"`
	RoutineID    string           `json:"routine_id"`
	Title        string           `json:"title"`
	What         string           `json:"what"`
	Mode         string           `json:"mode"`
	Schedule     *partnerSchedule `json:"schedule"`
	Target       string           `json:"target"`
	Deliver      string           `json:"deliver"`
	Why          string           `json:"why"`
	NextAction   string           `json:"next_action"`
	DoneLooksAs  string           `json:"done_looks_like"`
	Status       string           `json:"status"`
	Autopilot    *bool            `json:"autopilot"`
	Surface      string           `json:"surface"`
	Phone        bool             `json:"phone"`
	ProjectPath  string           `json:"project_path"`
}

func partnerToolDefs() []partnerToolDef {
	return []partnerToolDef{
		{
			id:   "children.list",
			risk: tools.RiskReadOnly,
			desc: "List your children (helpers you started, each with one job), their state and last report, plus how much " +
				"room this PC has for background work right now. Check this before starting children.",
			schema: `{"type":"object","properties":{` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolChildrenList,
		},
		{
			id:   "children.spawn",
			risk: tools.RiskMutation,
			desc: "Start a child: a separate conversation that does one job in the background and reports back into your conversation. " +
				"Returns immediately. Use it for work that takes a while or runs alongside the owner's chat — not for quick things you can do now. " +
				"Children share the PC: start only as many as children.list says there is room for. Children cannot start children.",
			schema: `{"type":"object","required":["task"],"properties":{
				"label":{"type":"string","maxLength":60,"description":"Short name the owner sees, e.g. 'Price check'."},
				"task":{"type":"string","minLength":1,"description":"The whole job, self-contained: the child does not see this conversation."},
				"goal_id":{"type":"string","description":"Optional goal this job serves."},
				` + partnerBoundProps + `},"additionalProperties":false}`,
			run: toolChildrenSpawn,
		},
		{
			id:   "children.message",
			risk: tools.RiskMutation,
			desc: "Send one of your children a follow-up (an answer, a correction, 'keep going'). It runs when the PC has room.",
			schema: `{"type":"object","required":["child_id","text"],"properties":{
				"child_id":{"type":"string"},
				"text":{"type":"string","minLength":1},
				` + partnerBoundProps + `},"additionalProperties":false}`,
			run: toolChildrenMessage,
		},
		{
			id:     "children.read",
			risk:   tools.RiskReadOnly,
			desc:   "Read the last messages of one child's conversation.",
			schema: `{"type":"object","required":["child_id"],"properties":{"child_id":{"type":"string"},"last":{"type":"integer","minimum":1,"maximum":20},` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolChildrenRead,
		},
		{
			id:     "children.stop",
			risk:   tools.RiskMutation,
			desc:   "Stop a child: cancel what it is doing and drop its waiting work.",
			schema: `{"type":"object","required":["child_id"],"properties":{"child_id":{"type":"string"},` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolChildrenStop,
		},
		{
			id:   "face.regenerate",
			risk: tools.RiskMutation,
			desc: "Give yourself a new face. Your face is drawn from a seed: it fixes your features, and the marks you have " +
				"earned with the owner land in new places. Use it only when the owner asks for a new or different face. " +
				"The old face is kept, and face.restore brings it back. Your growth is not reset.",
			schema: `{"type":"object","properties":{` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolFaceRegenerate,
		},
		{
			id:     "face.restore",
			risk:   tools.RiskMutation,
			desc:   "Bring back the face you had before the last face.regenerate. Use it when the owner liked the old one better.",
			schema: `{"type":"object","properties":{` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolFaceRestore,
		},
		{
			id:     "routines.list",
			risk:   tools.RiskReadOnly,
			desc:   "List the owner's standing routines with their schedule, next run and last result.",
			schema: `{"type":"object","properties":{` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolRoutinesList,
		},
		{
			id:   "routines.create",
			risk: tools.RiskMutation,
			desc: "Create a standing routine on a schedule in the owner's local time. mode=remind posts the text as a reminder with no model use — " +
				"prefer it whenever a reminder is enough. mode=task has you do the work each time. target: head (your own conversation, default), " +
				"child (a fresh child each run) or goal:<goal_id>. deliver: grove (default), messenger (the owner's chosen chat) or both.",
			schema: `{"type":"object","required":["what","schedule"],"properties":{
				"title":{"type":"string","maxLength":120},
				"what":{"type":"string","minLength":1,"description":"The reminder text, or the job to do each time."},
				"mode":{"type":"string","enum":["remind","task"]},
				"schedule":` + partnerScheduleSchema + `,
				"target":{"type":"string"},
				"deliver":{"type":"string","enum":["grove","messenger","both"]},
				` + partnerBoundProps + `},"additionalProperties":false}`,
			run: toolRoutinesCreate,
		},
		{
			id:     "routines.cancel",
			risk:   tools.RiskMutation,
			desc:   "Retire a routine for good.",
			schema: `{"type":"object","required":["routine_id"],"properties":{"routine_id":{"type":"string"},` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolRoutinesCancel,
		},
		{
			id:     "goals.list",
			risk:   tools.RiskReadOnly,
			desc:   "List the owner's goals (Grove plots) with status, next step, and whether autopilot is on.",
			schema: `{"type":"object","properties":{` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolGoalsList,
		},
		{
			id:   "goals.add",
			risk: tools.RiskMutation,
			desc: "Plant a goal for the owner in Grove. autopilot=true lets you keep taking steps on it in the background " +
				"(it pauses whenever it needs the owner).",
			schema: `{"type":"object","required":["title"],"properties":{
				"title":{"type":"string","minLength":1,"maxLength":200},
				"why":{"type":"string"},
				"next_action":{"type":"string"},
				"done_looks_like":{"type":"string"},
				"autopilot":{"type":"boolean"},
				` + partnerBoundProps + `},"additionalProperties":false}`,
			run: toolGoalsAdd,
		},
		{
			id:   "goals.update",
			risk: tools.RiskMutation,
			desc: "Update a goal: its next step, its status (open, active, paused, done, dropped), or autopilot on/off.",
			schema: `{"type":"object","required":["goal_id"],"properties":{
				"goal_id":{"type":"string"},
				"status":{"type":"string","enum":["open","active","paused","done","dropped"]},
				"next_action":{"type":"string"},
				"autopilot":{"type":"boolean"},
				` + partnerBoundProps + `},"additionalProperties":false}`,
			run: toolGoalsUpdate,
		},
		{
			id:   "owner.notify",
			risk: tools.RiskMutation,
			desc: "Leave the owner a short note on Grove's timeline. phone=true also sends it to the chat the owner chose for updates. " +
				"Use for things they should know without being asked — not for every step.",
			schema: `{"type":"object","required":["text"],"properties":{
				"title":{"type":"string","maxLength":120},
				"text":{"type":"string","minLength":1,"maxLength":1500},
				"phone":{"type":"boolean"},
				` + partnerBoundProps + `},"additionalProperties":false}`,
			run: toolOwnerNotify,
		},
		{
			id:     "grove.open",
			risk:   tools.RiskReadOnly,
			desc:   "Show the owner something in the app: a goal's room (goal_id) or a surface (grove or studio).",
			schema: `{"type":"object","properties":{"goal_id":{"type":"string"},"surface":{"type":"string","enum":["grove","studio"]},` + partnerBoundProps + `},"additionalProperties":false}`,
			run:    toolGroveOpen,
		},
	}
}

// RegisterPartnerTools installs Remedy's head tools on a registry.
func RegisterPartnerTools(registry *tools.Registry, server SettingsServerProvider) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", tools.ErrInvalidDescriptor)
	}
	if server == nil {
		return errors.New("partner server provider is required")
	}
	for _, def := range append(partnerToolDefs(), partnerSessionToolDefs()...) {
		def := def
		if _, err := registry.Latest(def.id); err == nil {
			continue
		}
		if err := registry.Register(tools.Descriptor{
			ID:          def.id,
			Version:     1,
			Description: def.desc,
			Runtime:     tools.RuntimeGo,
			Risk:        def.risk,
			InputSchema: json.RawMessage(def.schema),
			OutputSchema: json.RawMessage(`{"type":"object","required":["ok"],"properties":{
				"ok":{"type":"boolean"},"error":{"type":"string"}},"additionalProperties":true}`),
		}, tools.ExecutorFunc(func(_ context.Context, req tools.Request) (tools.Result, error) {
			s := server()
			var in partnerToolInput
			if len(req.Input) > 0 {
				if err := json.Unmarshal(req.Input, &in); err != nil {
					return partnerToolResult(nil, errors.New("invalid input"))
				}
			}
			if s == nil || s.partner == nil {
				return partnerToolResult(nil, errors.New("Remedy's background work is not available"))
			}
			out, err := def.run(s, in)
			return partnerToolResult(out, err)
		})); err != nil {
			return err
		}
	}
	return nil
}

func partnerToolResult(out any, err error) (tools.Result, error) {
	body := map[string]any{"ok": err == nil}
	if err != nil {
		body["error"] = err.Error()
	}
	if m, ok := out.(map[string]any); ok {
		for k, v := range m {
			body[k] = v
		}
	}
	raw, _ := json.Marshal(body)
	return tools.Result{Output: raw}, nil
}

// AttachPartnerTools registers the head tools on the cognition runner.
func (r *CognitionTurnRunner) AttachPartnerTools(server SettingsServerProvider) error {
	if r == nil || r.Registry == nil {
		return errors.New("cognition turn runner has no tool registry")
	}
	if err := RegisterPartnerTools(r.Registry, server); err != nil {
		return err
	}
	if err := RegisterMemorySkillTools(r.Registry, server); err != nil {
		return err
	}
	r.Tools = &RegistryToolExecutor{Registry: r.Registry, TokenFor: RuntimeCapabilityToken}
	r.Policy = &RegistryPolicy{Registry: r.Registry, Approvals: r.Approvals, LiveContext: r.LiveContext}
	r.syncModelToolSchemas()
	return nil
}

// ── executors ───────────────────────────────────────────────────────────

func childPublic(c partnerChild) map[string]any {
	return map[string]any{
		"child_id":    c.SessionID,
		"label":       c.Label,
		"task":        truncateRunes(c.Task, 300),
		"state":       c.State,
		"last_report": c.LastUpdate,
		"goal_id":     c.GoalID,
		"started_at":  c.CreatedAt,
	}
}

func toolChildrenList(s *Server, _ partnerToolInput) (any, error) {
	h := s.partner
	h.mu.Lock()
	rows := []map[string]any{}
	for _, c := range h.sortedChildrenLocked() {
		rows = append(rows, childPublic(*c))
	}
	h.mu.Unlock()
	capa := s.partnerCapacity()
	return map[string]any{"children": rows, "capacity": capa, "room": capa.Sentence()}, nil
}

func toolChildrenSpawn(s *Server, in partnerToolInput) (any, error) {
	parent := in.SessionID
	// A routine or autopilot run asking for a child still reports to the
	// head, not to the goal / routine conversation it happened to run in.
	child, err := s.partnerSpawnChild(childSpec{
		Label:           in.Label,
		Task:            in.Task,
		GoalID:          in.GoalID,
		ParentSessionID: parent,
		Origin:          in.CallerOrigin,
	})
	if err != nil {
		return nil, err
	}
	capa := s.partnerCapacity()
	note := "Started. It will report back into this conversation when it finishes."
	if capa.Free() == 0 {
		note = "Queued — " + capa.Sentence()
	}
	return map[string]any{"child": childPublic(child), "note": note}, nil
}

func toolChildrenMessage(s *Server, in partnerToolInput) (any, error) {
	if err := s.partnerMessageChild(in.ChildID, in.Text, in.CallerOrigin); err != nil {
		return nil, err
	}
	return map[string]any{"note": "Sent. The child picks it up when the PC has room."}, nil
}

func toolChildrenRead(s *Server, in partnerToolInput) (any, error) {
	h := s.partner
	h.mu.Lock()
	c := h.st.Children[strings.TrimSpace(in.ChildID)]
	h.mu.Unlock()
	if c == nil {
		return nil, errors.New("no child with that id — check children.list")
	}
	n := in.Last
	if n <= 0 || n > 20 {
		n = 6
	}
	msgs, err := s.sessions.ListMessages(c.SessionID, n, 0)
	if err != nil {
		return nil, err
	}
	rows := make([]map[string]any, 0, len(msgs))
	for _, m := range msgs {
		content, _ := m.Content.(string)
		rows = append(rows, map[string]any{"role": m.Role, "content": truncStr(content, 2000)})
	}
	return map[string]any{"child": childPublic(*c), "messages": rows}, nil
}

func toolChildrenStop(s *Server, in partnerToolInput) (any, error) {
	if !s.partnerStopChild(in.ChildID) {
		return nil, errors.New("no child with that id — check children.list")
	}
	return map[string]any{"note": "Stopped."}, nil
}

func routinePublic(r partnerRoutine) map[string]any {
	return map[string]any{
		"routine_id":   r.ID,
		"title":        r.Title,
		"what":         r.Prompt,
		"mode":         r.Mode,
		"when":         describeSchedule(r.Schedule),
		"schedule":     r.Schedule,
		"target":       r.Target,
		"deliver":      r.Deliver,
		"enabled":      r.Enabled,
		"next_run":     r.NextRun,
		"last_run":     r.LastRun,
		"last_state":   r.LastState,
		"last_summary": r.LastSummary,
		"runs":         r.Runs,
	}
}

func toolRoutinesList(s *Server, _ partnerToolInput) (any, error) {
	h := s.partner
	h.mu.Lock()
	rows := []map[string]any{}
	for _, r := range h.st.Routines {
		rows = append(rows, routinePublic(*r))
	}
	h.mu.Unlock()
	return map[string]any{"routines": rows}, nil
}

func toolRoutinesCreate(s *Server, in partnerToolInput) (any, error) {
	if in.Schedule == nil {
		return nil, errors.New("schedule is required")
	}
	r, err := s.partner.addRoutine(partnerRoutine{
		Title:     in.Title,
		Prompt:    in.What,
		Mode:      in.Mode,
		Schedule:  *in.Schedule,
		Target:    in.Target,
		Deliver:   in.Deliver,
		CreatedBy: originLabel(in.CallerOrigin),
		Outside:   !OriginIsOwner(in.CallerOrigin),
	})
	if err != nil {
		return nil, err
	}
	return map[string]any{"routine": routinePublic(*r)}, nil
}

func toolRoutinesCancel(s *Server, in partnerToolInput) (any, error) {
	if !s.partnerDeleteRoutine(in.RoutineID) {
		return nil, errors.New("no routine with that id — check routines.list")
	}
	return map[string]any{"note": "Retired."}, nil
}

func (s *Server) partnerDeleteRoutine(id string) bool {
	h := s.partner
	h.mu.Lock()
	defer h.mu.Unlock()
	i, r := h.findRoutineLocked(id)
	if r == nil {
		return false
	}
	h.st.Routines = append(h.st.Routines[:i], h.st.Routines[i+1:]...)
	kept := h.queue[:0]
	for _, q := range h.queue {
		if q.RoutineID != r.ID {
			kept = append(kept, q)
		}
	}
	h.queue = kept
	h.recordLocked(partnerActivity{Kind: "routine", Title: "Retired routine: " + r.Title, RoutineID: r.ID})
	h.saveLocked()
	return true
}

func toolGoalsList(s *Server, _ partnerToolInput) (any, error) {
	lifeGoalMu.Lock()
	f := s.loadLifeGoalsFile()
	lifeGoalMu.Unlock()
	h := s.partner
	h.mu.Lock()
	rows := []map[string]any{}
	for _, g := range f.Goals {
		row := map[string]any{
			"goal_id":     g.ID,
			"title":       g.Title,
			"status":      g.Status,
			"next_action": g.NextAction,
			"next_by":     g.NextBy,
		}
		if l := h.st.Goals[g.ID]; l != nil {
			row["autopilot"] = l.Autopilot
			row["state"] = l.State
			row["last_update"] = l.LastUpdate
		}
		rows = append(rows, row)
	}
	h.mu.Unlock()
	return map[string]any{"goals": rows}, nil
}

func toolGoalsAdd(s *Server, in partnerToolInput) (any, error) {
	g, err := s.addLifeGoal(in.Title, in.Why, "", in.NextAction, in.DoneLooksAs, "remedy")
	if err != nil {
		return nil, err
	}
	out := map[string]any{"goal": g.toPublic()}
	if in.Autopilot != nil && *in.Autopilot {
		link, err := s.partnerSetAutopilot(g.ID, true, 0)
		if err != nil {
			return nil, err
		}
		out["autopilot"] = link.Autopilot
	}
	s.partner.record(partnerActivity{Kind: "system", Title: "Planted a goal: " + g.Title, GoalID: g.ID})
	return out, nil
}

func toolGoalsUpdate(s *Server, in partnerToolInput) (any, error) {
	g, ok := s.findLifeGoal(in.GoalID)
	if !ok {
		return nil, errors.New("no goal with that id — check goals.list")
	}
	fields := map[string]any{}
	if st := strings.TrimSpace(in.Status); st != "" {
		fields["status"] = st
	}
	if na := strings.TrimSpace(in.NextAction); na != "" {
		fields["next_action"] = na
	}
	out := map[string]any{}
	if len(fields) > 0 {
		updated, ok := s.patchLifeGoal(g.ID, fields)
		if !ok {
			return nil, errors.New("goal not found")
		}
		out["goal"] = updated.toPublic()
	}
	if in.Autopilot != nil {
		link, err := s.partnerSetAutopilot(g.ID, *in.Autopilot, 0)
		if err != nil {
			return nil, err
		}
		out["autopilot"] = link.Autopilot
	}
	if len(out) == 0 {
		return nil, errors.New("nothing to change")
	}
	return out, nil
}

func toolOwnerNotify(s *Server, in partnerToolInput) (any, error) {
	text := strings.TrimSpace(in.Text)
	if text == "" {
		return nil, errors.New("text is required")
	}
	title := strings.TrimSpace(in.Title)
	if title == "" {
		title = "A note from Remedy"
	}
	s.partner.record(partnerActivity{Kind: "note", Title: title, Detail: text, SessionID: in.SessionID})
	phoned := false
	if in.Phone {
		phoned = s.partnerNotifyOwnerChannel(title + "\n" + text)
	}
	return map[string]any{"posted": true, "phoned": phoned}, nil
}

func toolGroveOpen(s *Server, in partnerToolInput) (any, error) {
	if s.appCmd == nil {
		return nil, errors.New("the app is not listening")
	}
	if gid := strings.TrimSpace(in.GoalID); gid != "" {
		g, ok := s.findLifeGoal(gid)
		if !ok {
			return nil, errors.New("no goal with that id")
		}
		return s.appCmd.Enqueue("open_goal", map[string]any{"goal_id": g.ID}), nil
	}
	surface := strings.TrimSpace(in.Surface)
	if surface == "" {
		surface = "grove"
	}
	return s.appCmd.Enqueue("switch_surface", map[string]any{"target": surface}), nil
}

// originLabel is a short "who asked" for routines Remedy creates.
func originLabel(origin string) string {
	o := strings.TrimSpace(origin)
	if o == "" {
		return "owner"
	}
	return truncateRunes(o, 60)
}

// partnerApprovalSummary is the owner-facing sentence on an approval card
// for one of Remedy's head tools: what she wants to do, in plain words.
func partnerApprovalSummary(name, preview string) string {
	var in partnerToolInput
	_ = json.Unmarshal([]byte(preview), &in)
	quote := func(s string, n int) string {
		s = strings.TrimSpace(s)
		if s == "" {
			return ""
		}
		return " “" + truncateRunes(s, n) + "”"
	}
	switch toolNameDotted(name) {
	case "children.spawn":
		label := in.Label
		if strings.TrimSpace(label) == "" {
			label = in.Task
		}
		return "Remedy wants to start a side job" + quote(label, 80) + ". It works in the background and reports back to her."
	case "children.message":
		return "Remedy wants to give one of her side jobs a follow-up" + quote(in.Text, 100) + "."
	case "children.stop":
		return "Remedy wants to stop one of her side jobs."
	case "routines.create":
		when := ""
		if in.Schedule != nil {
			if sc, err := normalizeSchedule(*in.Schedule); err == nil {
				when = " (" + strings.ToLower(describeSchedule(sc)[:1]) + describeSchedule(sc)[1:] + ")"
			}
		}
		if normalizeRoutineMode(in.Mode) == "remind" {
			return "Remedy wants to set a reminder" + quote(in.What, 100) + when + "."
		}
		return "Remedy wants to set up a standing routine" + quote(in.What, 100) + when + "."
	case "routines.cancel":
		return "Remedy wants to retire one of your routines."
	case "goals.add":
		if in.Autopilot != nil && *in.Autopilot {
			return "Remedy wants to plant a goal" + quote(in.Title, 80) + " and keep working on it on her own."
		}
		return "Remedy wants to plant a goal" + quote(in.Title, 80) + "."
	case "goals.update":
		switch {
		case in.Autopilot != nil && *in.Autopilot:
			return "Remedy wants to keep working on one of your goals on her own."
		case strings.TrimSpace(in.Status) != "":
			return "Remedy wants to mark one of your goals as " + strings.TrimSpace(in.Status) + "."
		}
		return "Remedy wants to update one of your goals."
	case "owner.notify":
		if in.Phone {
			return "Remedy wants to send you a note on your phone chat" + quote(in.Text, 100) + "."
		}
		return "Remedy wants to leave you a note" + quote(in.Text, 100) + "."
	}
	return "Remedy wants to manage her background work."
}
