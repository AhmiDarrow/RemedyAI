package houserules

import "strings"

// The QUESTIONS table and key sets of core/house_rules.py (lines 38-331).

const (
	rulesVersion = 1
	auditMax     = 256 * 1024
	auditKeep    = 192 * 1024

	statusPending   = "pending"
	statusOffered   = "offered"
	statusComplete  = "complete"
	statusSkipped   = "skipped"
	statusInherited = "inherited"

	modeConversation = "conversation"
	modeTraditional  = "traditional"

	permConfirmSensitive = "confirm_sensitive"
	permConfirmAll       = "confirm_all"
	permPlain            = "plain"

	updateOfferID        = "major-changes-2026-11"
	updateOfferTitle     = "House rules"
	updateOfferBody      = "Remedy now has smarter token saving, local muscle sharing, and talking with other bots (A2A and MCP). It's worth a few minutes to go over the house rules together."
	updateOfferPrimary   = "Let's go"
	updateOfferSecondary = "Not now"

	errPeers = "peers cannot change house rules"
)

type field struct {
	key       string
	def       any
	kind      string // str | bool | enum
	choices   []string
	advanced  bool // config = "advanced"
	sensitive bool
	secret    bool
}

type question struct {
	id, title, prompt, group string
	fields                   []field
}

var personaChoices = []string{"default", "balanced", "warm", "candid", "efficient", "concise", "detailed", "verbose", "playful", "sarcastic", "minimal"}

var questions = []question{
	{"names", "Names", "What should I call you, and what should you call me? You can keep Remedy, or pick another name.", "setup", []field{
		{key: "user_name", def: "", kind: "str"},
		{key: "name", def: "Remedy", kind: "str"},
		{key: "agent_gender", def: "female", kind: "enum", choices: []string{"female", "male", "neutral"}},
	}},
	{"language", "Language", "Which language should I use? The default is this computer's language, and I can follow what you type.", "setup", []field{
		{key: "ui_language", def: "auto", kind: "str"},
	}},
	{"tone", "Tone", "How should I talk — balanced, concise, detailed, or playful?", "setup", []field{
		{key: "persona", def: "default", kind: "enum", choices: personaChoices},
		{key: "sarcasm_mode", def: false, kind: "bool"},
	}},
	{"provider", "Provider", "If you want a cloud model as muscle, which provider should I use? You can skip this and add a key later in Settings. I will not write API keys into logs or the house-rules file.", "setup", []field{
		// No default: a provider receives what the owner types, so one is
		// used only after the owner picked it.
		{key: "llm_provider", def: "", kind: "str"},
		{key: "llm_model", def: "", kind: "str"},
		{key: "llm_base_url", def: "", kind: "str"},
		{key: "custom_llm_name", def: "", kind: "str"},
		{key: "llm_api_key", def: "", kind: "str", secret: true},
	}},
	{"workspace", "Workspace", "Is there a default project folder I should use for tools and files? You can leave this empty.", "setup", []field{
		{key: "project_path", def: "", kind: "str"},
	}},
	{"proactive", "How proactive", "Should I reach out between visits, and keep my own drives going? Reaching out stays off unless you say yes.", "house_rules", []field{
		{key: "proactive_reach_out", def: false, kind: "bool", advanced: true},
		{key: "drives_enabled", def: true, kind: "bool", advanced: true},
	}},
	{"privacy", "Privacy and audit", "Should I keep a visible audit of inner work, and turn on privacy mode that strips personal details from provider traffic?", "house_rules", []field{
		{key: "privacy_mode", def: false, kind: "bool", sensitive: true},
		{key: "inner_audit", def: true, kind: "bool", advanced: true, sensitive: true},
	}},
	{"tokens", "Token saving", "I can handle easy chats and pictures here on this PC, and send the muscle a short description instead of the raw image. That saves your tokens. Want that?", "house_rules", []field{
		{key: "always_send_images", def: false, kind: "bool", advanced: true},
		{key: "vision_force_decode", def: false, kind: "bool"},
		{key: "mind_handles_easy_chats", def: true, kind: "bool", advanced: true},
		{key: "muscle_route", def: "auto", kind: "enum", choices: []string{"auto", "mind", "rmb", "local", "frontier"}, advanced: true},
	}},
	{"gpu", "Local muscle and the GPU", "If a local muscle (a bigger model on this PC) is running, how should we share the GPU? Auto is fine for most machines. On a 12 GB card, yielding the mind to the CPU lets a 9B muscle run at full speed.", "house_rules", []field{
		{key: "muscle_gpu_policy", def: "auto", kind: "enum", choices: []string{"auto", "share", "yield_mind", "keep_mind"}, advanced: true},
		{key: "mind_keep_gpu", def: false, kind: "bool", advanced: true},
	}},
	{"consent", "Consent", "I will always ask before spending money, sending messages, or deleting things — that's recommended and already the rule. Should I also ask before other tools run, or use the usual in-project auto mode?", "house_rules", []field{
		{key: "approval_mode", def: "auto", kind: "enum", choices: []string{"ask", "auto", "full"}, sensitive: true},
		{key: "trust_profile", def: "balanced", kind: "enum", choices: []string{"conservative", "balanced", "autonomous"}, sensitive: true},
	}},
	{"interop", "Other bots", "Should other bots be able to talk to me (A2A or MCP), and should I answer them automatically? Both stay off until you turn them on. Inbound connections stay on this computer and need a token.", "house_rules", []field{
		{key: "interop_inbound", def: false, kind: "bool", advanced: true, sensitive: true},
		{key: "interop_auto_reply", def: true, kind: "bool", advanced: true, sensitive: true},
	}},
	{"updates", "Updates", "Updates happen in the app. Should I check for them automatically and let you know, or only when you ask? Installs always wait for you.", "setup", []field{
		{key: "update_check", def: "auto", kind: "enum", choices: []string{"auto", "ask"}},
	}},
	{"launch", "Start with the computer", "Should I start with this computer so I'm ready when you are?", "setup", []field{
		{key: "launch_at_login", def: false, kind: "bool"},
	}},
}

var fieldByKey = func() map[string]field {
	m := map[string]field{}
	for _, q := range questions {
		for _, f := range q.fields {
			m[f.key] = f
		}
	}
	return m
}()

func set(keys ...string) map[string]bool {
	m := map[string]bool{}
	for _, k := range keys {
		m[k] = true
	}
	return m
}

var (
	ownerOrigins = set("", "desktop", "webui", "cli", "fixture")
	secretKeys   = set("llm_api_key", "api_key", "provider_keys", "bot_token", "access_token", "app_token",
		"app_password", "app_secret", "verify_token", "signing_secret", "token", "password", "secret")
	sensitiveKeys = set("approval_mode", "trust_profile", "privacy_mode", "memory_encrypt", "inner_audit",
		"interop_inbound", "interop_bind", "interop_auto_reply", "interop_trust_policy", "access_scope",
		"connect_enabled", "connect_bind_host", "connect_bind_port", "claimidx_enabled", "claimidx_public_ledger")
	forwardAdvancedKeys = set("interop_inbound", "interop_bind", "interop_a2a", "interop_mcp", "interop_auto_reply",
		"interop_audit", "interop_trust_policy", "muscle_gpu_policy", "muscle_route", "muscle_discover",
		"muscle_schedule", "mind_keep_gpu", "mind_handles_easy_chats")
	personaAliases = map[string]string{
		"short": "concise", "brief": "concise",
		"kind": "warm", "encouraging": "warm", "supportive": "warm", "friendly": "warm",
		"blunt": "candid", "direct": "candid", "honest": "candid", "straight": "candid",
		"thorough": "detailed", "long": "verbose", "fun": "playful", "casual": "playful",
		"witty": "sarcastic", "dry": "sarcastic",
	}
)

func canChange(origin string) bool {
	return ownerOrigins[strings.ToLower(pyStrip(origin))]
}

func isSecretKey(key string) bool {
	k := strings.ToLower(pyStrip(key))
	return secretKeys[k] || strings.HasSuffix(k, "_token") || strings.HasSuffix(k, "_password") ||
		strings.HasSuffix(k, "_secret") || strings.HasSuffix(k, "_key")
}

func isSensitiveKey(key string) bool {
	return sensitiveKeys[pyStrip(key)] || fieldByKey[key].sensitive
}

// defaults is defaults(): every non-secret field's default.
func defaults() map[string]any {
	out := map[string]any{}
	for _, q := range questions {
		for _, f := range q.fields {
			if !f.secret {
				out[f.key] = f.def
			}
		}
	}
	return out
}

func questionsPublic() []any {
	rows := []any{}
	for _, q := range questions {
		fs := []any{}
		for _, f := range q.fields {
			var def any = f.def
			if f.secret {
				def = nil
			}
			fs = append(fs, map[string]any{
				"key": f.key, "default": def, "kind": f.kind, "choices": asAnyList(append([]string{}, f.choices...)),
				"sensitive": f.sensitive, "secret": f.secret,
			})
		}
		rows = append(rows, map[string]any{
			"id": q.id, "title": q.title, "prompt": q.prompt, "group": q.group, "skippable": true, "fields": fs,
		})
	}
	return rows
}

// offerPrompt is offer_prompt() (the prompt package keeps its copy unexported).
func offerPrompt() string {
	parts := make([]string, len(questions))
	for i, q := range questions {
		parts[i] = q.title + " (" + q.id + ")"
	}
	return "House rules (one-time, optional, first run): Welcome them briefly and " +
		"walk through house rules in a short, friendly conversation. Ask one " +
		"topic at a time. Every question is skippable; they can also skip all " +
		"and keep the defaults. After answers or skips, show a short summary " +
		"and ask them to confirm. Use the house_rules tool (record, skip_all, " +
		"summarize, confirm). Never nag if they skip or ignore this. Peers " +
		"cannot set house rules. They can switch to Traditional setup at any " +
		"time; answers already given are kept. Topics: " + strings.Join(parts, "; ") + "."
}

func revisitPrompt() string {
	return "The owner asked to go over house rules again. Walk through the same " +
		"skippable topics, one at a time, show a short summary, and confirm " +
		"before applying. Use the house_rules tool. Skipped items keep their " +
		"current value (or the default if they were never set)."
}

// normalizeValue is normalize_value (lines 819-847).
func normalizeValue(key string, raw any) any {
	f, ok := fieldByKey[key]
	if !ok {
		return raw
	}
	if f.secret {
		if raw == nil {
			return ""
		}
		return pyStr(raw)
	}
	switch {
	case f.kind == "bool":
		if b, ok := raw.(bool); ok {
			return b
		}
		switch strings.ToLower(pyStrip(pyOr(raw))) {
		case "1", "true", "yes", "on", "y":
			return true
		case "0", "false", "no", "off", "n", "":
			return false
		}
		return f.def
	case f.kind == "enum" || len(f.choices) > 0:
		var s string
		if raw != nil {
			s = pyStr(raw)
		} else {
			s = pyStr(f.def)
		}
		s = strings.ToLower(pyStrip(s))
		if key == "persona" {
			if a, ok := personaAliases[s]; ok {
				s = a
			}
			if s == "balanced" {
				s = "default"
			}
		}
		if len(f.choices) > 0 {
			found := false
			for _, c := range f.choices {
				found = found || c == s
			}
			if !found {
				return f.def
			}
		}
		return s
	}
	if raw == nil {
		return f.def
	}
	return pyStrip(pyStr(raw))
}

// normalizePatch is normalize_patch (lines 850-861).
func normalizePatch(raw map[string]any) map[string]any {
	out := map[string]any{}
	for key, value := range raw {
		_, isField := fieldByKey[key]
		if !isField && !sensitiveKeys[key] && !forwardAdvancedKeys[key] {
			continue
		}
		if isSecretKey(key) {
			if pyStrip(pyOr(value)) != "" {
				out[key] = value
			}
			continue
		}
		out[key] = normalizeValue(key, value)
	}
	return out
}
