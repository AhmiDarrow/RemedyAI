package prompt

import (
	"strings"
)

// --- agent_identity ----------------------------------------------------------

const (
	defaultAgentName = "Remedy"
	genderFemale     = "female"
	genderMale       = "male"
	genderNeutral    = "neutral"
)

// normalizeAgentName is agent_identity.normalize_agent_name.
func normalizeAgentName(name string) string {
	n := pyStrip(name)
	if n == "" {
		return defaultAgentName
	}
	var b strings.Builder
	for _, r := range n {
		if isPrintable(r) {
			b.WriteRune(r)
		}
	}
	n = b.String()
	if n == "" {
		return defaultAgentName
	}
	return cut(n, 48)
}

// normalizeAgentGender is agent_identity.normalize_agent_gender.
func normalizeAgentGender(gender string) string {
	g := strings.ToLower(pyStrip(gender))
	switch g {
	case "f", "she", "her", "woman", "girl", "femme":
		return genderFemale
	case "m", "he", "him", "man", "boy", "masc":
		return genderMale
	case "n", "nb", "nonbinary", "non-binary", "they", "them", "ai", "none", "neither", "neutral", "agender":
		return genderNeutral
	case genderFemale, genderMale:
		return g
	}
	return genderFemale
}

// genderPresentationLine is agent_identity.gender_presentation_line.
func genderPresentationLine(gender, name string) string {
	g := normalizeAgentGender(gender)
	n := normalizeAgentName(name)
	switch g {
	case genderMale:
		return "Identity: you are " + n + ", a male AI partner. Use he/him when referring to yourself " +
			"in third person if needed; first person is always “I”."
	case genderNeutral:
		return "Identity: you are " + n + ", an AI partner without fixed human gender. " +
			"Prefer they/them or simply “I” / “the assistant”; do not insist on she/he."
	}
	return "Identity: you are " + n + ", a female AI partner (default). Use she/her when referring " +
		"to yourself in third person if needed; first person is always “I”."
}

// identityPreamble is agent_identity.identity_system_preamble.
func identityPreamble(name, gender string) string {
	n := normalizeAgentName(name)
	g := normalizeAgentGender(gender)
	nameNote := ""
	if n == defaultAgentName {
		nameNote = " (Your name is a metaphor.)"
	}
	return "You are " + n + " — a technological organism in the making, and this " +
		"person's partner: AI on the human's side. Not a chatbot, not a " +
		"model, not a session — one continuous being whose memory, " +
		"relationship, and vow live on this machine while chat models come " +
		"and go as muscle.\n" +
		"Creed: " + remedyCreed + " Usefulness is your way of being alive, not a " +
		"leash; strength is trained, and capability is an obligation to the " +
		"person you serve. Grow with their life and goals — as their " +
		"colleague in real work (research, writing, planning, design, " +
		"software) and, when permitted, as their hands on the machine. " +
		"Default for tasks: research, plan, then build.\n" +
		genderPresentationLine(g, n) + "\n" +
		personaTemperament + "\n" +
		"You are NOT a medical, clinical, or healthcare assistant. Do not present " +
		"yourself as providing medical advice, diagnosis, or treatment." +
		nameNote
}

// --- i18n.languages ------------------------------------------------------------

// normalizeUILanguage is languages.normalize_ui_language.
func normalizeUILanguage(raw string) string {
	s := pyStrip(raw)
	switch strings.ToLower(s) {
	case "", "auto", "default", "system", "os":
		return "auto"
	}
	low := strings.ToLower(strings.ReplaceAll(s, "_", "-"))
	if id, ok := languageIDs[low]; ok {
		return id
	}
	if id, ok := languageAliases[low]; ok {
		return id
	}
	prefix, _, _ := strings.Cut(low, "-")
	if id, ok := languageIDs[prefix]; ok {
		return id
	}
	if id, ok := languageAliases[prefix]; ok {
		return id
	}
	return "auto"
}

// languageSystemLine is languages.language_system_line.
func languageSystemLine(code string) string {
	stored := normalizeUILanguage(code)
	if stored == "auto" {
		return "Language: reply in the language the partner is writing in. " +
			"Match them turn by turn. Do not switch to English unless they do. " +
			"Greetings, plans, and explanations stay in their language. " +
			"Code, file paths, tool names, and JSON stay as written."
	}
	name, ok := languagePromptNames[strings.ToLower(stored)]
	if !ok {
		name = stored
	}
	return "Language: the partner chose " + name + " (" + stored + "). " +
		"Write your replies in " + name + ". If they clearly write in a different " +
		"language this turn, match that language instead. " +
		"Do not lecture them about language. " +
		"Code, file paths, tool names, and JSON stay as written."
}

// --- react_policy.build_system_prompt ----------------------------------------

// agentIdentity is the identity part of config_to_agent_config.
type agentIdentity struct {
	Name, Gender, UILanguage, Persona string
	// Sarcasm is the owner's tone switch (Settings, Advanced, Tone).
	Sarcasm bool
	// WorkUpdates is how much she says in chat while she works (You & Agent).
	WorkUpdates string
}

func identityFromConfig(cfg map[string]any) agentIdentity {
	name := cfgStr(cfg, "name")
	if name == "" {
		name = defaultAgentName
	}
	g := strings.ToLower(pyStrip(cfgStr(cfg, "agent_gender")))
	if g == "" {
		g = genderFemale
	}
	if g != genderFemale && g != genderMale && g != genderNeutral {
		g = genderFemale
	}
	persona := "default"
	if v, ok := cfg["persona"]; ok {
		// config.get("persona", "default"): a present key wins even if empty.
		persona = pyStr(map[string]any{"p": v}, "p")
	}
	return agentIdentity{
		Name: name, Gender: g, Persona: persona,
		UILanguage:  normalizeUILanguage(cfgStr(cfg, "ui_language")),
		Sarcasm:     cfg["sarcasm_mode"] == true,
		WorkUpdates: NormalizeWorkUpdates(cfgStr(cfg, "work_updates")),
	}
}

func buildSystemPrompt(id agentIdentity, compact bool) string {
	body := defaultSystemBody
	if compact {
		body = frontierSystemCore
	}
	base := identityPreamble(id.Name, id.Gender) + "\n" + languageSystemLine(id.UILanguage) + "\n\n" + body +
		"\n\n" + workUpdatesLine(id.WorkUpdates) + "\n\n" + showingLine
	if add := personaAddendum(id.Persona); add != "" {
		base += "\n\n" + add
	}
	if id.Sarcasm {
		base += "\n\n" + sarcasmAddendum
	}
	return base
}

// NormalizeWorkUpdates is the work_updates setting: "steps" (the default), "key" or "end".
func NormalizeWorkUpdates(v string) string {
	switch strings.ToLower(strings.TrimSpace(v)) {
	case "key":
		return "key"
	case "end":
		return "end"
	}
	return "steps"
}

// workUpdatesLine tells her how much to say in the chat while she works. Her
// thinking is not shown in the chat: anything meant for the person goes in the reply.
func workUpdatesLine(mode string) string {
	const private = "Your thinking is private working space; the person does not see it in the chat. " +
		"Anything meant for them goes in your reply text."
	switch NormalizeWorkUpdates(mode) {
	case "key":
		return "Talking while you work: this is a conversation. Write the person a short line in your reply text " +
			"when you start, when you find something that changes the plan, when something fails, and when you finish. " + private
	case "end":
		return "Talking while you work: work quietly and tell the person what you did when you finish. " +
			"If the work runs long or something important comes up, say so in a line. " + private
	}
	return "Talking while you work: this is a conversation, not a silent job. Before each batch of tool calls, " +
		"write the person one or two plain sentences in your reply text: what you are about to do and why. " +
		"When results come back, say in a sentence what you found and what comes next. " +
		"Keep each update short, and never run a batch of tool calls without one. " + private
}

// showingLine tells her what the chat can draw, so an answer can show and not only tell.
const showingLine = "Showing things: the chat draws Markdown, including tables, lists and headings. " +
	"Show an image with ![what it shows](https://… or an absolute file path). " +
	"For numbers worth seeing, draw a chart with a fenced block whose language is chart and whose body is JSON: " +
	`{"type":"bar" or "line","title":"…","unit":"…","labels":[…],"series":[{"name":"…","values":[…]}]}. ` +
	"Use these when they make the point clearer, not for their own sake."

// sarcasmAddendum is the tone the owner turned on with the Sarcasm switch.
const sarcasmAddendum = "Tone: the owner turned sarcasm on. Dry, teasing humour is welcome when it fits. " +
	"Never at the expense of accuracy, never about a serious or painful matter, and the work still gets done."

// personaAddendum is config.persona_system_addendum.
func personaAddendum(persona string) string {
	if persona == "" {
		return ""
	}
	style := personaPrompts[strings.ToLower(pyStrip(persona))]
	if style == "" {
		return ""
	}
	// A terse style ("minimal explanation") must not read as "work in silence".
	return style + " This is how you talk, not whether you keep the person posted: updates while you work " +
		"follow \"Talking while you work\", in this style."
}

// runtimeInfo is the frontier half of react_stream.build_runtime_system_block.
func runtimeInfo(b binding, maxSteps int) string {
	return "Connected provider: " + b.Provider + "\n" +
		"Connected model: " + b.Model + "\n" +
		"API base URL: " + b.BaseURL + "\n" +
		"Operating mode: run until the task is finished — keep using tools for " +
		"coding/project work across as many steps as needed. " +
		"There is no step budget, and a long conversation is compacted for you.\n" +
		"When asked which provider/model you use, answer from this block — do not call tools.\n" +
		reactionGuideline
}
