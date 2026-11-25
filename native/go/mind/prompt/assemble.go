package prompt

import (
	"strings"
)

// prompt.assemble (runtime/prompt_assemble._assemble_async).

// Recent-conversation caps (REMEDY_FULL_CONTEXT on, the default).
const (
	historyBlockCharCap = 48_000
	historyEntryCharCap = 16_000
	historyMaxEntries   = 40
	historyHeader       = "Recent conversation (oldest first; the current message follows separately " +
		"— do not repeat it):"
	// react_policy.REACT_MAX_TOTAL_STEPS without the REMEDY_REACT_MAX_TOTAL_STEPS override.
	maxReactSteps = 1_000_000
)

type historyRow struct {
	Role, Content string
}

type assembleInput struct {
	Message     string
	SessionID   string
	PlanMode    bool
	ChatMode    bool
	ProjectPath string
	Provider    string
	Model       string
	BaseURL     string
	History     []historyRow
	historyRaw  bool
}

func parseAssembleInput(in map[string]any) assembleInput {
	msg := pyStr(in, "message")
	if msg == "" {
		msg = pyStr(in, "prompt")
	}
	out := assembleInput{
		Message:     msg,
		SessionID:   pyStrip(pyStr(in, "session_id")),
		PlanMode:    boolIn(in, "plan_mode"),
		ChatMode:    boolIn(in, "chat_mode"),
		ProjectPath: pyStrip(pyStr(in, "project_path")),
		Provider:    pyStrip(pyStr(in, "provider")),
		Model:       pyStrip(pyStr(in, "model")),
		BaseURL:     pyStrip(pyStr(in, "base_url")),
	}
	if rows, ok := in["history"].([]any); ok {
		out.historyRaw = true
		for _, r := range rows {
			m, ok := r.(map[string]any)
			if !ok {
				continue
			}
			out.History = append(out.History, historyRow{Role: pyStr(m, "role"), Content: pyStr(m, "content")})
		}
	}
	return out
}

// renderHistoryBlock is prompt_assemble.render_history_block.
func renderHistoryBlock(rows []historyRow) string {
	type kept struct{ role, content string }
	var all []kept
	for _, r := range rows {
		role := strings.ToLower(pyStrip(r.Role))
		if role != "user" && role != "assistant" {
			continue
		}
		content := pyStrip(r.Content)
		if content == "" {
			continue
		}
		if cpLen(content) > historyEntryCharCap {
			content = pyRstrip(cut(content, historyEntryCharCap-1)) + "…"
		}
		all = append(all, kept{role, content})
	}
	if len(all) > historyMaxEntries {
		all = all[len(all)-historyMaxEntries:]
	}
	if len(all) == 0 {
		return ""
	}
	budget := max(0, historyBlockCharCap-cpLen(historyHeader)-1)
	var lines []string
	used := 0
	for i := len(all) - 1; i >= 0; i-- {
		line := "[" + all[i].role + "] " + all[i].content
		need := cpLen(line) + 1
		if used+need > budget {
			room := budget - used - 1
			if room >= 400 {
				lines = append(lines, pyRstrip(cut(line, room-1))+"…")
			}
			break
		}
		lines = append(lines, line)
		used += need
	}
	if len(lines) == 0 {
		return ""
	}
	for i, j := 0, len(lines)-1; i < j; i, j = i+1, j-1 {
		lines[i], lines[j] = lines[j], lines[i]
	}
	return historyHeader + "\n" + strings.Join(lines, "\n")
}

// turn is everything the sections of one assemble share.
type turn struct {
	env      Env
	in       assembleInput
	bind     binding
	muscle   muscle
	id       agentIdentity
	proj     project
	planMode bool
	// chatOnly is runtime_turn_is_chat_only (empty text is not chat-only).
	chatOnly bool
}

func newTurn(env Env, in assembleInput) (*turn, error) {
	t := &turn{env: env, in: in}
	t.bind = resolveBinding(env.Config, in)
	t.muscle = classifyMuscle(t.bind)
	t.id = identityFromConfig(env.Config)
	proj, err := resolveProject(env, in.ProjectPath)
	if err != nil {
		return nil, err
	}
	t.proj = proj
	t.planMode = in.PlanMode && !in.ChatMode
	t.chatOnly = turnIsChatOnly(cut(in.Message, 4000))
	return t, nil
}

func (t *turn) soulRequest() soulRequest {
	budget := 1200
	if t.muscle.denseMemory {
		budget = 1800
	}
	return soulRequest{
		contract:    !t.chatOnly,
		workThreads: !t.chatOnly,
		provider:    t.bind.Provider,
		model:       t.bind.Model,
		userName:    pyStrip(cfgStr(t.env.Config, "user_name")),
		maxChars:    budget,
		projectPath: t.proj.Active,
		sessionID:   t.in.SessionID,
	}
}

// soulFieldEnabled is feature_maturity.soul_field_enabled (default on).
func soulFieldEnabled(cfg map[string]any) bool {
	v, ok := cfg["soul_field_enabled"]
	if !ok {
		return true
	}
	switch x := v.(type) {
	case bool:
		return x
	case float64:
		return x != 0
	case int64:
		return x != 0
	case int:
		return x != 0
	}
	s := strings.ToLower(pyStrip(pyStr(map[string]any{"v": v}, "v")))
	switch s {
	case "1", "true", "yes", "on", "enable", "enabled":
		return true
	case "0", "false", "no", "off", "disable", "disabled", "":
		return false
	}
	return true
}

// approvalMode is approvals.normalize_approval_mode over the configured mode
// (BasicRuntime stamps APPROVALS from config approval_mode, default auto).
func approvalMode(cfg map[string]any) string {
	m := strings.ToLower(pyStrip(cfgStr(cfg, "approval_mode")))
	if m == "" {
		m = "auto"
	}
	switch m {
	case "full", "warn", "owner", "yolo", "unrestricted":
		return "full"
	case "auto", "auto-approve", "thumbs-up", "trust":
		return "auto"
	case "ask":
		return "ask"
	}
	return "ask"
}

var skillGreetings = map[string]bool{
	"hi": true, "hey": true, "hello": true, "thanks": true, "thank you": true, "ok": true,
	"okay": true, "yes": true, "no": true, "yep": true, "nope": true, "sure": true, "cool": true, "bye": true,
}

// skillsPart is the skills catalog part of build_turn_context.
func (t *turn) skillsPart() (string, []string) {
	reg := t.env.Skills
	count := 0
	if reg != nil {
		count = reg.Count()
	}
	if reg == nil || count <= 0 {
		return "Skills loaded: (none yet — bundled defaults load on server start).", nil
	}
	taskQ := cut(pyStrip(cut(t.in.Message, 4000)), 240)
	if taskQ != "" && (cpLen(taskQ) < 4 || skillGreetings[strings.TrimRight(strings.ToLower(taskQ), "!.?")]) {
		taskQ = ""
	}
	var lines []string
	if st := t.env.State; st != nil && len(st.WarmSkills) >= 3 && taskQ == "" {
		lines = append(lines, st.WarmSkills[:min(24, len(st.WarmSkills))]...)
	}
	if len(lines) == 0 {
		for _, row := range reg.Rank(taskQ, 24, t.proj.Active) {
			desc := pyStrip(row.Description)
			if cpLen(desc) > 140 {
				desc = cut(desc, 137) + "…"
			}
			lines = append(lines, "- **"+row.Name+"** ["+row.Status+"]: "+desc)
		}
		if len(lines) > 0 {
			lines = append(lines, "_Activate with skill.activate(name=…); rank with skill.search._")
		} else {
			// registry.summary_lines is not ported.
			return "", []string{"skills:summary_lines"}
		}
		if st := t.env.State; st != nil {
			st.WarmSkills = append([]string(nil), lines...)
		}
	}
	return "Skills catalog (name+status only — call skill.activate to load " +
		"ONE full procedure for the current task; skill.search to rank — " +
		"never skill.activate every pack):\n" + strings.Join(lines, "\n"), nil
}

// staticMissing are producers in build_turn_context and the assemble tail
// that this port does not build, and whether each can contribute this turn.
func (t *turn) staticMissing() []string {
	miss := []string{
		// work_roots / orientation / stack fingerprint (core/project_fingerprint.py, game_engines).
		"work_roots",
		// Partner Memory: profile ranking, reinforce and taste folding write back.
		"partner_memory",
		"project_chapter",
		"partner_state",
		// Middleman working memory over the CAS object store.
		"middleman",
		"recent_memory",
		// _trim_context_parts needs the nanoswarm BPE token estimate.
		"context_trim",
		// Addenda: plan store, build todos, companion, taste, away, inbox,
		// computer-use guidance (browse intent parser) and the per-host skill hint.
		"active_plan", "build_todos", "companion", "taste", "away", "companion_inbox",
		"computer_use_addendum", "computer_skill",
		// prime_turn_metabolism: tier, house organs, time crystal, organism pulse.
		"metabolism",
		// remedy_core host lines (stretch census, host dialect) in the workspace block.
		"host_lines",
	}
	if t.in.Message != "" {
		miss = append(miss, "recall")
	}
	if !t.chatOnly {
		miss = append(miss, "build_resume", "session_brief", "continuity")
	}
	return miss
}

func assembleMissing(env Env, in assembleInput) []string {
	t, err := newTurn(env, in)
	if err != nil {
		return []string{"workspace:" + err.Error()}
	}
	if isLocalBinding(t.bind) {
		return []string{"local_slim_system"}
	}
	miss := t.staticMissing()
	if soulFieldEnabled(env.Config) {
		miss = append(miss, soulMissing(env, t.soulRequest())...)
		miss = append(miss, aliveMissing(env)...)
	}
	return sortedUnique(miss)
}

// Assemble is prompt.assemble. Sections the port does not build are named
// in Missing and left out; the output is the worker's only when Missing is
// empty. It performs the same side effects as Python for the sections it
// builds (the one-time vigil offer, the inner-language audit line, the
// project folder).
func Assemble(env Env, input map[string]any) (Result, error) {
	in := parseAssembleInput(input)
	t, err := newTurn(env, in)
	if err != nil {
		return Result{}, err
	}
	var secs []Section
	missing := []string{}
	if isLocalBinding(t.bind) {
		// slim_system_for_local rewrites the whole block; not ported.
		missing = append(missing, "local_slim_system")
	} else {
		missing = append(missing, t.staticMissing()...)
		secs = append(secs,
			Section{"system_prompt", "", buildSystemPrompt(t.id, true)},
			Section{"runtime_info", "\n\n", runtimeInfo(t.bind, maxReactSteps)},
			Section{"turn_marker", "", turnContextMarker},
		)
		ctx, miss := t.contextParts()
		missing = append(missing, miss...)
		for i, p := range ctx {
			if i > 0 {
				p.Sep = "\n\n"
			}
			secs = append(secs, p)
		}
		secs = append(secs, t.addenda()...)
	}
	if in.historyRaw {
		if h := renderHistoryBlock(in.History); h != "" {
			// The block before it is rstripped first.
			if n := len(secs); n > 0 {
				secs[n-1].Text = pyRstrip(secs[n-1].Text)
			}
			secs = append(secs, Section{"history", "\n\n", h})
		}
	}
	system := Join(secs)
	contextChars := 0
	historyChars := 0
	inContext := false
	for _, s := range secs {
		switch {
		case s.Name == "turn_marker":
			inContext = true
			continue
		case s.Name == "history":
			historyChars = cpLen(s.Text)
			continue
		}
		if inContext {
			contextChars += cpLen(s.Sep) + cpLen(s.Text)
		}
	}
	return Result{
		Output: map[string]any{
			"system": system, "goal": in.Message,
			"context_chars": contextChars, "system_chars": cpLen(system), "history_chars": historyChars,
		},
		Sections: secs,
		Missing:  sortedUnique(missing),
	}, nil
}

// contextParts is build_turn_context for the ported producers, in order.
func (t *turn) contextParts() ([]Section, []string) {
	var parts []Section
	var miss []string
	add := func(name, text string) {
		if text != "" {
			parts = append(parts, Section{Name: name, Text: text})
		}
	}
	add("isolation", isolationLine(t.in.SessionID, t.proj.Active))
	if soulFieldEnabled(t.env.Config) {
		req := t.soulRequest()
		if m := soulMissing(t.env, req); len(m) > 0 {
			miss = append(miss, m...)
		} else if s, err := soulBlock(t.env, req); err != nil {
			miss = append(miss, "soul:"+err.Error())
		} else {
			add("soul", s)
		}
		if m := aliveMissing(t.env); len(m) > 0 {
			miss = append(miss, m...)
		} else if a, err := aliveInject(t.env); err != nil {
			miss = append(miss, "alive:"+err.Error())
		} else {
			add("alive", a)
		}
	}
	if !t.chatOnly {
		add("builder", builderAddendum(t.muscle))
	}
	add("workspace", workspaceBlock(t.proj))
	if t.proj.Unset || t.proj.Scope == "full" {
		add("access_reminder", "Access scope: full (or no focus folder). "+
			"Tools may use absolute paths anywhere allowed for this account. "+
			"A focus folder is optional convenience for relative paths — "+
			"not required. Prefer reversible writes; confirm destructive ops.")
	}
	switch approvalMode(t.env.Config) {
	case "full":
		add("approvals", "Approvals: Full (warn). The host is in Remedy's hands. "+
			"Write and run anywhere this account can. Auth secrets "+
			"(~/.remedy/auth) stay closed. Do not stop to ask. "+
			"write / edit / bash now "+
			"(use only Tool ABI ids in your schema).")
	case "auto":
		add("approvals", "Approvals: Auto (in-project). write / edit "+
			"/ bash for pytest / uv / ruff inside the focus folder "+
			"run without prompts. OS, home, and sibling trees stay jailed "+
			"unless the user clicks Full.")
	}
	if sid := t.in.SessionID; strings.HasPrefix(sid, "msg:") && strings.Contains(sid[4:], ":") {
		origin := strings.SplitN(sid, ":", 3)[1]
		if origin != "" {
			add("messenger", "[Messenger surface: "+origin+"] "+
				"You are the same Remedy as desktop chat for this person. "+
				"Keep replies concise for chat apps; still use tools when work needs them. "+
				"Do not introduce yourself as a new bot.")
		}
	}
	if isRMBProvider(t.bind.Provider, t.bind.BaseURL) {
		add("rmb", "RMB local agent: You are the sole on-device model for this session. "+
			"No separate vision stack — image attachments are file paths; use tools "+
			"to inspect them. Context is managed automatically (Session Brief, prune, "+
			"offload) — do not discuss memory pressure or ask the user to compress. "+
			"Finish tool chains, keep working, prefer compact tool results and "+
			"concrete paths.")
	}
	if !t.chatOnly {
		add("self_config", "Self-configuration: when the user asks you to set up, enable, disable, "+
			"change, or configure Remedy (web tools, approval mode, model/provider, "+
			"vision, persona, their name, project folder, access scope, messengers, "+
			"assistant prefs, RMB, house map, etc.), use Desktop Settings / product "+
			"HTTP surfaces when those actions are not in your Tool ABI schemas. "+
			"Do not invent snake_case settings tools. Prefer computer.* / clipboard.* "+
			"from your schema for screen and clipboard work.")
		add("durable_memory", "Durable memory: when the user says remember / note that / don't forget / "+
			"store in memory, ALWAYS call memory.save(content=…) with the fact "+
			"(in addition to any automatic silent save). Confirm briefly what was stored. "+
			"Never store secrets or API keys. Use memory.search to look up prior facts.")
		add("manual", "Owner's manual / F1 Help: you CAN and SHOULD read it. Prefer "+
			"read on docs/manual paths under the product tree when those "+
			"paths are in access scope. Never claim F1/help is outside access scope "+
			"for read-only manuals shipped with the product.")
	}
	skills, m := t.skillsPart()
	miss = append(miss, m...)
	add("skills", skills)
	return parts, miss
}

// addenda are the ported blocks of append_plan_and_computer_addenda.
func (t *turn) addenda() []Section {
	msg := coerceText(t.in.Message)
	if t.planMode {
		return []Section{{"plan_mode_addendum", "\n\n", planModeAddendum}}
	}
	chatOnly := isChatOnlyMessage(msg) || (msg != "" && isFeelingPresenceQuestion(msg))
	if chatOnly {
		return nil
	}
	if t.muscle.tier >= 3 {
		return []Section{{"frontier_build_addendum", "\n\n", frontierBuildAddendum}}
	}
	return []Section{{"build_mode_addendum", "\n\n", buildModeAddendum}}
}
