package prompt

import (
	"strings"
)

// The native prompt. Assemble (assemble.go) reproduces the Python worker's
// single system string so its section builders can be checked against
// recorded output. AssembleNative is what the runtime sends. It uses the same
// builders and lays them out for how providers cache a request (tools, then
// system, then messages, matched by prefix):
//
//   - System holds only what stays the same for a session: identity, rules,
//     approvals, mode. A provider caches it once and reads it back cheaply.
//   - Context holds what changes turn to turn: her state, the workspace
//     listing, memory, the plan. The runtime places it just before the
//     owner's message, so earlier turns stay a stable, cacheable prefix.
//
// Sections that only restated another section, or whose data nothing in the
// Go runtime writes, are not built (see docs/WORKER_REVIEW.md).

// Sources is the runtime-owned data the prompt reads. Every method returns a
// finished block, or "" when there is nothing to show.
type Sources interface {
	// MemoryBlock is the owner's durable facts ranked for query, followed by
	// anything else the message recalls.
	MemoryBlock(query, projectPath string) string
	// ActivePlan is the session's open task plan.
	ActivePlan(sessionID string) string
	// Todos is the session's checklist while any item is open.
	Todos(sessionID string) string
}

// Native is one turn's prompt in the runtime's layout.
type Native struct {
	// System is the stable system prompt.
	System string
	// Context is this turn's context, or "" when there is none (a local
	// model's slim system carries everything).
	Context string
	// Goal is the owner's message.
	Goal string
	// Sections are the pieces of System, then of Context, for tests and
	// diagnostics. Context sections follow the one named ContextMarker.
	Sections []Section
}

// ContextMarker names the section that opens Context.
const ContextMarker = "turn_marker"

// contextHeader opens the per-turn note. It says what the note is, because a
// model reads it in the conversation, not in the system prompt.
const contextHeader = "[Turn context — from Remedy's runtime, not from the owner. " +
	"Current as of this message; it replaces any earlier turn context.]"

// AssembleNative builds the prompt for one turn.
func AssembleNative(env Env, input map[string]any) (Native, error) {
	in := parseAssembleInput(input)
	t, err := newTurn(env, in)
	if err != nil {
		return Native{}, err
	}
	// The owner's Chat switch, not a guess from the wording, decides whether
	// the working rules are present: a guess would change the system prompt
	// from message to message and defeat the cache.
	chat := in.ChatMode
	msg := coerceText(in.Message)

	var sys, ctx []Section
	add := func(dst *[]Section, name, text string) {
		if strings.TrimSpace(text) == "" {
			return
		}
		sep := ""
		if len(*dst) > 0 {
			sep = "\n\n"
		}
		*dst = append(*dst, Section{Name: name, Sep: sep, Text: text})
	}

	add(&sys, "system_prompt", buildSystemPrompt(t.id, true))
	add(&sys, "runtime_info", runtimeInfo(t.bind, maxReactSteps))
	add(&sys, "isolation", isolationLine(in.SessionID, t.proj.Active))
	soulOn := soulFieldEnabled(env.Config)
	if soulOn {
		name := pyStrip(normalizeAgentName(cfgStr(env.Config, "name")))
		add(&sys, "contract", providerMuscleContract(t.bind.Provider, t.bind.Model, name))
	}
	if !chat {
		add(&sys, "builder", builderAddendum(t.muscle))
	}
	for _, s := range t.ruleSections(chat) {
		add(&sys, s.Name, s.Text)
	}
	switch {
	case t.planMode:
		add(&sys, "plan_mode_addendum", planModeAddendum)
	case chat:
	case t.muscle.tier >= 3:
		add(&sys, "frontier_build_addendum", frontierBuildAddendum)
	default:
		add(&sys, "build_mode_addendum", buildModeAddendum)
	}

	add(&ctx, ContextMarker, contextHeader)
	// What Remedy knows about the owner goes only into the owner's own turns.
	if soulOn && !in.Outside {
		req := t.soulRequest()
		req.contract = false
		req.offers = true
		req.workThreads = !chat
		if s, err := soulBlock(env, req); err == nil {
			add(&ctx, "soul", s)
		}
		// An unreadable state file reads as a fresh one, as the Python did.
		if a, err := aliveInject(env); err == nil {
			add(&ctx, "alive", a)
		}
	}
	add(&ctx, "workspace", workspaceBlock(t.proj))
	if !chat {
		add(&ctx, "work_roots", workRootsBlock(t.proj.Active))
	}
	if src := env.Sources; src != nil {
		if !in.Outside {
			add(&ctx, "memory", src.MemoryBlock(cut(pyStrip(msg), 400), t.proj.Active))
		}
		if !chat {
			add(&ctx, "active_plan", src.ActivePlan(in.SessionID))
			add(&ctx, "todos", src.Todos(in.SessionID))
		}
	}
	skills, _ := t.skillsPart()
	add(&ctx, "skills", skills)
	if !chat && !t.planMode {
		add(&ctx, "away", awayGuidance(msg))
		add(&ctx, "computer_use", computerUseGuidance(msg))
	}
	if isChatOnlyMessage(msg) || (msg != "" && isFeelingPresenceQuestion(msg)) {
		// Small talk in a working mode: the rules above still apply, but this
		// message wants an answer, not a build.
		add(&ctx, "brief", "Reply to the person. Be brief.")
	}

	out := Native{Goal: in.Message}
	if isLocalBinding(t.bind) {
		// A small local model gets one short system written for its window
		// instead of the two-part layout.
		out.System = slimForLocalBinding(t, sys, ctx)
		out.Sections = []Section{{Name: "local_slim_system", Text: out.System}}
		return out, nil
	}
	out.System = Join(sys)
	if len(ctx) > 1 {
		out.Context = Join(ctx)
	}
	out.Sections = append(sys, ctx...)
	return out, nil
}

// ruleSections are the standing rules that follow from configuration alone.
func (t *turn) ruleSections(chat bool) []Section {
	var out []Section
	add := func(name, text string) { out = append(out, Section{Name: name, Text: text}) }
	if t.proj.Unset || t.proj.Scope == "full" {
		add("access_reminder", accessReminderText)
	}
	switch approvalMode(t.env.Config) {
	case "full":
		add("approvals", approvalsFullText)
	case "auto":
		add("approvals", approvalsAutoText)
	}
	if sid := t.in.SessionID; strings.HasPrefix(sid, "msg:") && strings.Contains(sid[4:], ":") {
		if origin := strings.SplitN(sid, ":", 3)[1]; origin != "" {
			add("messenger", messengerText(origin))
		}
	}
	if isRMBProvider(t.bind.Provider, t.bind.BaseURL) {
		add("rmb", rmbText)
	}
	if !chat {
		add("self_config", selfConfigText)
		add("durable_memory", durableMemoryText)
		add("manual", manualText)
	}
	return out
}

// slimForLocalBinding rewrites the two-part prompt into the one short system
// a small local model gets. The identity and runtime lines are the head; the
// rules and this turn's context are the body it trims to the model's window.
func slimForLocalBinding(t *turn, sys []Section, ctx []Section) string {
	head, body := sys, []Section(nil)
	if len(sys) > 2 {
		head, body = sys[:2], append(body, sys[2:]...)
	}
	for _, s := range ctx {
		if s.Name != ContextMarker {
			body = append(body, s)
		}
	}
	for i := range body {
		body[i].Sep = "\n\n"
	}
	if len(body) > 0 {
		body[0].Sep = ""
	}
	return slimSystemForLocal(Join(head), Join(body), t.bind.Provider, t.bind.Model,
		maxReactSteps, t.in.Message, t.env.LocalWindow)
}
