package httpapi

import (
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"regexp"
	"sort"
	"strconv"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
)

// The rest of what Remedy can do, as commands: every setting, what the model
// side is using and costing, and the state of the PC's services. Like the
// commands in session_command_more.go they call the runtime's own endpoints
// in-process (selfCall), so they show and change exactly what the Settings
// screens and panels do.

// fullSlashCommands is listed by /help after moreSlashCommands.
var fullSlashCommands = []map[string]any{
	// settings
	{"name": "/settings", "description": "List settings and their values: /settings [word to filter by]", "aliases": []string{"/config"}, "arguments": "filter", "group": "Settings"},
	{"name": "/set", "description": "Change a setting: /set <name> <value> (names from /settings)", "aliases": []string{}, "arguments": "name value", "group": "Settings"},
	{"name": "/get", "description": "Show one setting: /get <name>", "aliases": []string{}, "arguments": "name", "group": "Settings"},
	{"name": "/theme", "description": "Show or set the look: /theme <name>", "aliases": []string{}, "arguments": "name", "group": "Settings"},
	{"name": "/name", "description": "What she calls you: /name <your name>", "aliases": []string{}, "arguments": "name", "group": "Settings"},
	{"name": "/partner", "description": "Her name: /partner <name>", "aliases": []string{}, "arguments": "name", "group": "Settings"},
	{"name": "/persona", "description": "How she talks: /persona <style>", "aliases": []string{}, "arguments": "style", "group": "Settings"},
	{"name": "/language", "description": "The language of the menus: /language <code>", "aliases": []string{"/lang"}, "arguments": "code", "group": "Settings"},
	{"name": "/web", "description": "Let her search and read the web: /web on|off", "aliases": []string{}, "arguments": "on|off", "group": "Settings"},
	{"name": "/privacy", "description": "Privacy mode: /privacy on|off", "aliases": []string{}, "arguments": "on|off", "group": "Settings"},
	{"name": "/approvals", "description": "When she asks before acting: /approvals ask|auto|full", "aliases": []string{"/mode"}, "arguments": "ask|auto|full", "group": "Settings"},
	{"name": "/scope", "description": "Which folders her tools may use: /scope project|workspace|full", "aliases": []string{}, "arguments": "project|workspace|full", "group": "Settings"},
	{"name": "/effort", "description": "Reasoning effort for models that take one: /effort <level>", "aliases": []string{}, "arguments": "level", "group": "Settings"},
	{"name": "/learning", "description": "Let her write new skills from what works: /learning on|off", "aliases": []string{}, "arguments": "on|off", "group": "Settings"},
	{"name": "/vigil", "description": "Night work while you are away: /vigil on|off", "aliases": []string{}, "arguments": "on|off", "group": "Settings"},
	{"name": "/safeguards", "description": "The four safeguards and their values", "aliases": []string{"/guards"}, "arguments": nil, "group": "Settings"},
	// the model side
	{"name": "/usage", "description": "Tokens and estimated cost: /usage [days]", "aliases": []string{"/cost"}, "arguments": "days", "group": "Model"},
	{"name": "/context", "description": "How full this conversation's context is", "aliases": []string{}, "arguments": nil, "group": "Model"},
	{"name": "/providers", "description": "Model providers and which are connected", "aliases": []string{}, "arguments": nil, "group": "Model"},
	{"name": "/tools", "description": "The tools she can use: /tools [word to filter by]", "aliases": []string{}, "arguments": "filter", "group": "Model"},
	{"name": "/agents", "description": "The agent kinds a conversation can run as", "aliases": []string{}, "arguments": nil, "group": "Model"},
	{"name": "/mcp", "description": "Connected MCP servers", "aliases": []string{}, "arguments": nil, "group": "Model"},
	{"name": "/todos", "description": "Her checklist in this conversation", "aliases": []string{}, "arguments": nil, "group": "Model"},
	// memory
	{"name": "/forget", "description": "Remove something from memory: /forget <text>", "aliases": []string{}, "arguments": "text", "group": "Memory"},
	// her own work
	{"name": "/routines", "description": "Her standing routines and when they next run", "aliases": []string{}, "arguments": nil, "group": "Her work"},
	{"name": "/jobs", "description": "Background jobs she has running", "aliases": []string{"/children"}, "arguments": nil, "group": "Her work"},
	{"name": "/notifications", "description": "What she left for you while you were away", "aliases": []string{"/inbox"}, "arguments": nil, "group": "Her work"},
	// this PC
	{"name": "/connect", "description": "Phone access: /connect, or /connect pause | resume", "aliases": []string{"/phone"}, "arguments": "pause|resume", "group": "This PC"},
	{"name": "/voice", "description": "Speaking and listening: what is installed and switched on", "aliases": []string{}, "arguments": nil, "group": "This PC"},
	{"name": "/doctor", "description": "A health check of Remedy and this PC", "aliases": []string{"/diagnostics"}, "arguments": nil, "group": "This PC"},
	{"name": "/version", "description": "The version that is running", "aliases": []string{}, "arguments": nil, "group": "This PC"},
	{"name": "/update", "description": "Check whether a newer version is out", "aliases": []string{"/updates"}, "arguments": nil, "group": "This PC"},
	{"name": "/commands", "description": "Every command, grouped", "aliases": []string{}, "arguments": nil, "group": "Help"},
}

// allSlashCommands is every command the runtime carries out, in help order.
// /api/commands serves it, so a command palette never offers one that does nothing.
func allSlashCommands() []map[string]any {
	out := make([]map[string]any, 0, len(builtinSlashCommands)+len(moreSlashCommands)+len(fullSlashCommands))
	out = append(out, builtinSlashCommands...)
	out = append(out, moreSlashCommands...)
	out = append(out, fullSlashCommands...)
	return out
}

// settingShortcuts maps a command to the setting it shows and sets.
var settingShortcuts = map[string]string{
	"/theme": "theme", "/name": "user_name", "/partner": "name", "/persona": "persona",
	"/language": "ui_language", "/lang": "ui_language", "/web": "web_tools_enabled",
	"/privacy": "privacy_mode", "/approvals": "approval_mode", "/mode": "approval_mode",
	"/scope": "access_scope", "/effort": "reasoning_effort", "/learning": "allow_skill_creation",
	"/vigil": "vigil_enabled",
}

// Settings a command never writes: facts about the install, and secrets, which
// are entered in Settings where they are not echoed into a conversation.
var settingReadOnly = map[string]bool{
	"config_exists": true, "config_path": true, "llm_api_key_set": true, "llm_ready": true,
	"needs_setup": true, "provider_keys_set": true, "secrets_encoding": true,
	"secrets_encoding_warning": true, "version": true, "advanced_schema": true,
	"ui_languages": true, "default_workspace_path": true, "setup_completed": true,
}

var secretishKey = regexp.MustCompile(`(?i)(secret|token|password|passwd|api_?key|private|credential)`)

func scalarText(v any) (string, bool) {
	switch t := v.(type) {
	case nil:
		return "", false
	case string:
		if strings.TrimSpace(t) == "" {
			return "(empty)", true
		}
		return truncateRunes(t, 160), true
	case bool:
		if t {
			return "on", true
		}
		return "off", true
	case float64:
		return strconv.FormatFloat(t, 'f', -1, 64), true
	case json.Number:
		return t.String(), true
	}
	return "", false
}

// renderFacts turns an answer into short "name: value" lines, a level or two
// deep, leaving out anything that looks like a secret.
func renderFacts(v any, indent string, depth int) []string {
	var lines []string
	switch t := v.(type) {
	case map[string]any:
		keys := make([]string, 0, len(t))
		for k := range t {
			keys = append(keys, k)
		}
		sort.Strings(keys)
		for _, k := range keys {
			if secretishKey.MatchString(k) {
				continue
			}
			label := strings.ReplaceAll(k, "_", " ")
			if text, ok := scalarText(t[k]); ok {
				lines = append(lines, indent+label+": "+text)
				continue
			}
			if depth <= 0 {
				continue
			}
			if sub := renderFacts(t[k], indent+"  ", depth-1); len(sub) > 0 {
				lines = append(lines, indent+label+":")
				lines = append(lines, sub...)
			}
		}
	case []any:
		for i, item := range t {
			if i == 12 {
				lines = append(lines, fmt.Sprintf("%s… and %d more", indent, len(t)-i))
				break
			}
			if text, ok := scalarText(item); ok {
				lines = append(lines, indent+text)
				continue
			}
			if row, ok := item.(map[string]any); ok {
				lines = append(lines, indent+rowLine(row))
			}
		}
	}
	return lines
}

// rowLine is one list item on one line: its name, then a few telling fields.
func rowLine(row map[string]any) string {
	name := ""
	for _, k := range []string{"title", "label", "name", "id"} {
		if s, _ := row[k].(string); strings.TrimSpace(s) != "" {
			name = s
			break
		}
	}
	var bits []string
	for _, k := range []string{"state", "status", "connected", "enabled", "schedule", "next_run", "last_update", "description", "detail", "text", "message"} {
		if secretishKey.MatchString(k) {
			continue
		}
		if text, ok := scalarText(row[k]); ok && text != "(empty)" {
			bits = append(bits, strings.ReplaceAll(k, "_", " ")+" "+truncateRunes(text, 80))
		}
		if len(bits) == 3 {
			break
		}
	}
	if name == "" {
		name = "(unnamed)"
	}
	if len(bits) == 0 {
		return "**" + truncateRunes(name, 80) + "**"
	}
	return "**" + truncateRunes(name, 80) + "** — " + strings.Join(bits, ", ")
}

func onOff(word string) (value bool, ok bool) {
	switch strings.ToLower(strings.TrimSpace(word)) {
	case "on", "yes", "true", "1", "enable", "enabled":
		return true, true
	case "off", "no", "false", "0", "disable", "disabled":
		return false, true
	}
	return false, false
}

// settingsNow is the settings as the Settings screen reads them.
func (who slashCaller) settingsNow() map[string]any {
	_, out := who.call(http.MethodGet, "/api/settings", nil)
	return out
}

// lookupSetting finds a setting by name, top level or "advanced.<name>" (the
// prefix may be left out).
func lookupSetting(all map[string]any, name string) (key string, advanced bool, value any, found bool) {
	n := strings.ToLower(strings.TrimSpace(name))
	n = strings.ReplaceAll(strings.ReplaceAll(n, "-", "_"), " ", "_")
	adv, _ := all["advanced"].(map[string]any)
	if rest, ok := strings.CutPrefix(n, "advanced."); ok {
		v, has := adv[rest]
		return rest, true, v, has
	}
	if v, has := all[n]; has {
		return n, false, v, true
	}
	if v, has := adv[n]; has {
		return n, true, v, true
	}
	return n, false, nil, false
}

// coerceSetting turns the typed value into the kind the setting already has.
func coerceSetting(current any, typed string) (any, error) {
	typed = strings.TrimSpace(typed)
	switch current.(type) {
	case bool:
		if b, ok := onOff(typed); ok {
			return b, nil
		}
		return nil, fmt.Errorf("this one takes on or off")
	case float64, json.Number:
		n, err := strconv.ParseFloat(typed, 64)
		if err != nil {
			return nil, fmt.Errorf("this one takes a number")
		}
		return n, nil
	case string, nil:
		return typed, nil
	}
	return nil, fmt.Errorf("this one is a list or a group and is changed in Settings")
}

func (who slashCaller) showSetting(name string) map[string]any {
	all := who.settingsNow()
	key, adv, value, found := lookupSetting(all, name)
	if !found {
		return map[string]any{"text": "There is no setting called “" + name + "”. Send /settings " + name + " to look for it."}
	}
	if secretishKey.MatchString(key) {
		return map[string]any{"text": "That one is a secret and is not shown in a conversation."}
	}
	text, ok := scalarText(value)
	if !ok {
		return map[string]any{"text": "**" + key + "** is a list or a group; open Settings to see it."}
	}
	if adv {
		key = "advanced." + key
	}
	return map[string]any{"text": "**" + key + "** is " + text + "."}
}

func (who slashCaller) setSetting(name, typed string) map[string]any {
	// A key or password never goes through a conversation, whatever it is called.
	if secretishKey.MatchString(name) {
		return map[string]any{"text": "**" + strings.TrimSpace(name) + "** is not changed by a command. Keys and passwords are entered in Settings, where they are not written into a conversation."}
	}
	all := who.settingsNow()
	key, adv, current, found := lookupSetting(all, name)
	if !found {
		return map[string]any{"text": "There is no setting called “" + name + "”. Send /settings to see the names."}
	}
	if settingReadOnly[key] || secretishKey.MatchString(key) {
		return map[string]any{"text": "**" + key + "** is not changed by a command. Keys and passwords are entered in Settings, where they are not written into a conversation."}
	}
	value, err := coerceSetting(current, typed)
	if err != nil {
		return map[string]any{"text": "**" + key + "**: " + err.Error() + "."}
	}
	var body map[string]any
	if adv {
		body = map[string]any{"advanced": map[string]any{key: value}}
	} else {
		body = map[string]any{key: value}
	}
	code, out := who.call(http.MethodPut, "/api/settings", body)
	if code >= 300 {
		return map[string]any{"text": "Could not change **" + key + "**: " + detailOf(out, "the runtime refused")}
	}
	// Read it back: a setting can be normalized or refused without an error.
	_, _, after, _ := lookupSetting(who.settingsNow(), name)
	text, _ := scalarText(after)
	return map[string]any{"text": "**" + key + "** is now " + text + ".", "action": "settings_changed", "setting": key, "value": after}
}

func (who slashCaller) listSettings(filter string) map[string]any {
	all := who.settingsNow()
	f := strings.ToLower(strings.TrimSpace(filter))
	match := func(k string) bool {
		return f == "" || strings.Contains(strings.ReplaceAll(k, "_", " "), f) || strings.Contains(k, f)
	}
	var lines []string
	keys := make([]string, 0, len(all))
	for k := range all {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	for _, k := range keys {
		if settingReadOnly[k] || secretishKey.MatchString(k) || !match(k) {
			continue
		}
		if text, ok := scalarText(all[k]); ok {
			lines = append(lines, "  "+k+": "+text)
		}
	}
	// The long tail of advanced settings is shown only when asked for by a word.
	if adv, _ := all["advanced"].(map[string]any); f != "" {
		akeys := make([]string, 0, len(adv))
		for k := range adv {
			akeys = append(akeys, k)
		}
		sort.Strings(akeys)
		for _, k := range akeys {
			if secretishKey.MatchString(k) || !match(k) {
				continue
			}
			if text, ok := scalarText(adv[k]); ok {
				lines = append(lines, "  advanced."+k+": "+text)
			}
		}
	}
	if len(lines) == 0 {
		return map[string]any{"text": "No setting matches “" + filter + "”."}
	}
	head := "Settings:"
	if f != "" {
		head = "Settings matching “" + filter + "”:"
	}
	tail := "\n\nChange one with /set <name> <value>."
	if f == "" {
		tail += " There are more under advanced: send /settings and a word to find them."
	}
	if len(lines) > 80 {
		lines = append(lines[:80], fmt.Sprintf("  … and %d more; add a word to narrow it down", len(lines)-80))
	}
	return map[string]any{"text": head + "\n" + strings.Join(lines, "\n") + tail}
}

// executeFullSlash runs one of fullSlashCommands, or returns nil for another command.
func (s *Server) executeFullSlash(sessionID, raw string, phone bool) map[string]any {
	who := slashCaller{s: s, phone: phone}
	fields := strings.Fields(raw)
	if len(fields) == 0 {
		return nil
	}
	name := strings.ToLower(fields[0])
	arg := strings.TrimSpace(strings.TrimPrefix(strings.TrimSpace(raw), fields[0]))
	say := func(format string, a ...any) map[string]any {
		return map[string]any{"text": fmt.Sprintf(format, a...)}
	}
	facts := func(title string, v any, depth int) map[string]any {
		lines := renderFacts(v, "  ", depth)
		if len(lines) == 0 {
			return say("%s\n  nothing to show.", title)
		}
		return say("%s\n%s", title, strings.Join(lines, "\n"))
	}

	if key, ok := settingShortcuts[name]; ok {
		if arg == "" {
			return who.showSetting(key)
		}
		return who.setSetting(key, arg)
	}

	switch name {
	case "/settings", "/config":
		return who.listSettings(arg)
	case "/get":
		if arg == "" {
			return say("Name the setting: /get <name>.")
		}
		return who.showSetting(arg)
	case "/set":
		parts := strings.Fields(arg)
		if len(parts) < 2 {
			return say("Give the name and the value: /set <name> <value>. Send /settings to see the names.")
		}
		return who.setSetting(parts[0], strings.TrimSpace(strings.TrimPrefix(arg, parts[0])))
	case "/safeguards", "/guards":
		g := loadToolGuards(LoadConfig(s.homeDir))
		return say("Safeguards:\n  shell_in_auto: %s\n  web_fetch_guard: %s\n  messenger_tools: %s\n  peer_tools: %s\n  api_max_body_mb: %d\n\nChange one with /set <name> <value>. What each does is in docs/SECURITY.md.",
			g.Shell, g.WebFetch, g.Messenger, g.Peer, apiMaxBodyMB(LoadConfig(s.homeDir)))

	case "/usage", "/cost":
		days := 7
		if n, err := strconv.Atoi(strings.TrimSuffix(strings.ToLower(arg), "d")); err == nil && n > 0 && n <= 365 {
			days = n
		}
		_, out := who.call(http.MethodGet, fmt.Sprintf("/api/usage/summary?range_days=%d", days), nil)
		totals, _ := out["totals"].(map[string]any)
		num := func(k string) float64 { f, _ := totals[k].(float64); return f }
		lines := []string{fmt.Sprintf("The last %d days:", days),
			fmt.Sprintf("  %s tokens in, %s out, %s in all", groupDigits(num("prompt_tokens")), groupDigits(num("completion_tokens")), groupDigits(num("total_tokens"))),
			fmt.Sprintf("  over %s model rounds", groupDigits(num("events")))}
		if cost := num("estimated_cost_usd"); cost > 0 {
			lines = append(lines, fmt.Sprintf("  about $%.2f at list prices", cost))
		}
		if rows, _ := out["by_model"].([]any); len(rows) > 0 {
			lines = append(lines, "", "By model:")
			for i, r := range rows {
				if i == 8 {
					break
				}
				row, _ := r.(map[string]any)
				tok, _ := row["total_tokens"].(float64)
				lines = append(lines, fmt.Sprintf("  %v: %s tokens", firstOf(row, "model", "name", "id"), groupDigits(tok)))
			}
		}
		lines = append(lines, "", "These are the counts the provider reported. Remedy does not work out a price; your provider's bill has that.")
		return say("%s", strings.Join(lines, "\n"))

	case "/context":
		if sessionID == "" || s.sessions == nil {
			return say("Open a conversation first, then send /context.")
		}
		msgs, err := s.sessions.ListMessages(sessionID, 500, 0)
		if err != nil {
			return say("Could not read this conversation: %s", err.Error())
		}
		chars := 0
		for _, m := range msgs {
			chars += len(contentAsString(m.Content))
		}
		approx := float64(chars) / 4
		window := float64(cfgInt(LoadConfig(s.homeDir), "context_window_tokens", 0))
		line := fmt.Sprintf("This conversation has %d messages, roughly %s tokens of text.", len(msgs), groupDigits(approx))
		if window > 0 {
			line += fmt.Sprintf(" That is about %.0f%% of a %s-token window.", 100*approx/window, groupDigits(window))
		}
		return say("%s\nRemedy also sends her instructions, tools and memory with each turn, so the real figure is higher. She shortens old turns by herself when it fills up; /clear starts it empty.", line)

	case "/providers":
		_, out := who.call(http.MethodGet, "/api/providers/connected", nil)
		lines := []string{fmt.Sprintf("In use: %v · %v", out["active_provider"], out["active_model"]), "", "Connected:"}
		rows, _ := out["connected"].([]any)
		for _, r := range rows {
			row, _ := r.(map[string]any)
			lines = append(lines, fmt.Sprintf("  %v", firstOf(row, "label", "name", "id")))
		}
		if len(rows) == 0 {
			lines = append(lines, "  none yet. Connect one in Settings.")
		}
		lines = append(lines, "", "Switch a conversation's model with /model.")
		return say("%s", strings.Join(lines, "\n"))

	case "/tools":
		_, out := who.call(http.MethodGet, "/api/tools", nil)
		rows, _ := out["tools"].([]any)
		f := strings.ToLower(arg)
		var names []string
		for _, r := range rows {
			row, _ := r.(map[string]any)
			id := fmt.Sprint(firstOf(row, "id", "name"))
			if f == "" || strings.Contains(strings.ToLower(id), f) {
				names = append(names, id)
			}
		}
		sort.Strings(names)
		if len(names) == 0 {
			return say("No tool matches “%s”.", arg)
		}
		return say("%d tools:\n  %s", len(names), strings.Join(names, ", "))

	case "/agents":
		_, out := who.call(http.MethodGet, "/api/agents", nil)
		return facts("Agent kinds:", out["agents"], 1)

	case "/mcp":
		servers, _ := LoadConfig(s.homeDir)["mcp_servers"].([]any)
		if len(servers) == 0 {
			return say("No MCP servers are connected. Add one in Settings, Connections.")
		}
		lines := []string{"MCP servers:"}
		for _, sv := range servers {
			if row, ok := sv.(map[string]any); ok {
				lines = append(lines, "  "+fmt.Sprint(firstOf(row, "name", "id", "command", "url")))
			} else {
				lines = append(lines, "  "+fmt.Sprint(sv))
			}
		}
		return say("%s", strings.Join(lines, "\n"))

	case "/todos":
		if sessionID == "" {
			return say("Open a conversation first, then send /todos.")
		}
		_, out := who.call(http.MethodGet, "/api/sessions/"+url.PathEscape(sessionID)+"/todos", nil)
		rows, _ := out["todos"].([]any)
		if len(rows) == 0 {
			return say("She has no checklist in this conversation.")
		}
		lines := []string{"Her checklist:"}
		for _, r := range rows {
			row, _ := r.(map[string]any)
			mark := "[ ]"
			switch fmt.Sprint(row["status"]) {
			case "completed", "done":
				mark = "[x]"
			case "in_progress":
				mark = "[>]"
			}
			lines = append(lines, fmt.Sprintf("  %s %v", mark, firstOf(row, "content", "title", "text")))
		}
		return say("%s", strings.Join(lines, "\n"))

	case "/forget":
		if arg == "" {
			return say("Say what to forget: /forget <text>. /memory <words> shows what is there.")
		}
		if s.sessions == nil {
			return say("Memory is not available right now.")
		}
		found, err := s.sessions.searchMemoryLIKE(arg, 6)
		if err != nil {
			return say("Could not search memory: %s", err.Error())
		}
		switch {
		case len(found) == 0:
			return say("Nothing in memory contains “%s”.", arg)
		case len(found) > 3:
			lines := []string{fmt.Sprintf("More than three things in memory contain “%s”. Give more of the text so only the ones you mean match:", arg)}
			for _, e := range found {
				lines = append(lines, "  "+truncateRunes(e.Title+" — "+e.Content, 120))
			}
			return say("%s", strings.Join(lines, "\n"))
		}
		n, err := s.sessions.forgetMemoryLIKE(arg)
		if err != nil {
			return say("Could not remove it: %s", err.Error())
		}
		lines := []string{fmt.Sprintf("Removed %d from memory:", n)}
		for _, e := range found {
			lines = append(lines, "  "+truncateRunes(e.Title+" — "+e.Content, 120))
		}
		return map[string]any{"text": strings.Join(lines, "\n"), "action": "memory_changed"}

	case "/routines":
		out, err := toolRoutinesList(s, partnerToolInput{SessionID: sessionID})
		if err != nil {
			return say("%s", err.Error())
		}
		raw, _ := json.Marshal(out)
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		if rows, _ := m["routines"].([]any); len(rows) == 0 {
			return say("She has no standing routines. Ask her for one, for example “every weekday at 7, brief me”.")
		}
		return facts("Routines:", m["routines"], 1)

	case "/jobs", "/children":
		out, err := toolChildrenList(s, partnerToolInput{SessionID: sessionID})
		if err != nil {
			return say("%s", err.Error())
		}
		raw, _ := json.Marshal(out)
		var m map[string]any
		_ = json.Unmarshal(raw, &m)
		rows, _ := m["children"].([]any)
		if len(rows) == 0 {
			return say("She has no background jobs right now.")
		}
		return facts("Background jobs:", rows, 1)

	case "/notifications", "/inbox":
		_, out := who.call(http.MethodGet, "/api/notifications", nil)
		rows, _ := out["notifications"].([]any)
		if len(rows) == 0 {
			return say("Nothing is waiting for you.")
		}
		return facts(fmt.Sprintf("%d for you:", len(rows)), rows, 1)

	case "/connect", "/phone":
		switch strings.ToLower(arg) {
		case "pause":
			if code, out := who.call(http.MethodPost, "/api/connect/pause", map[string]any{}); code >= 300 {
				return say("Could not pause it: %s", detailOf(out, "the runtime refused"))
			}
			return map[string]any{"text": "Phone access is paused. The pairing is kept; /connect resume turns it back on.", "action": "connect_changed"}
		case "resume":
			if code, out := who.call(http.MethodPost, "/api/connect/resume", map[string]any{}); code >= 300 {
				return say("Could not resume it: %s", detailOf(out, "the runtime refused"))
			}
			return map[string]any{"text": "Phone access is on again.", "action": "connect_changed"}
		case "":
		default:
			return say("/connect takes pause or resume. Pairing a new phone is done in Settings, Connect, on the PC.")
		}
		code, out := who.call(http.MethodGet, "/api/connect", nil)
		if code >= 300 {
			return say("%s", detailOf(out, "Phone access cannot be shown from here."))
		}
		devices, _ := out["devices"].([]any)
		lines := []string{"Phone access:"}
		if on, _ := out["enabled"].(bool); !on {
			lines = append(lines, "  off")
		} else if paused, _ := out["paused"].(bool); paused {
			lines = append(lines, "  paused")
		} else {
			lines = append(lines, "  on")
		}
		lines = append(lines, fmt.Sprintf("  %d phone(s) paired", len(devices)))
		if mobile, _ := out["mobile"].(map[string]any); mobile != nil {
			if st, _ := mobile["status"].(map[string]any); st != nil {
				if msg, _ := st["message"].(string); msg != "" {
					lines = append(lines, "  away from home: "+msg)
				}
			}
		}
		return say("%s", strings.Join(lines, "\n"))

	case "/voice":
		_, out := who.call(http.MethodGet, "/api/voice/status", nil)
		set, _ := out["settings"].(map[string]any)
		tts, _ := out["tts"].(map[string]any)
		stt, _ := out["stt"].(map[string]any)
		yes := func(m map[string]any, k string) string {
			if b, _ := m[k].(bool); b {
				return "yes"
			}
			return "no"
		}
		return say("Voice:\n  speaking: installed %s, switched on %s, voice %v\n  listening: installed %s, switched on %s, model %v\n  reads replies aloud: %s\n\nSwitches are in Settings, Voice.",
			yes(tts, "installed"), yes(set, "tts_enabled"), tts["voice"],
			yes(stt, "installed"), yes(set, "stt_enabled"), stt["model"], yes(set, "speak_replies"))

	case "/doctor", "/diagnostics":
		_, out := who.call(http.MethodGet, "/api/diagnostics", nil)
		lines := []string{fmt.Sprintf("Overall: %v", out["overall"])}
		if issues, _ := out["issues"].([]any); len(issues) > 0 {
			lines = append(lines, "", "To look at:")
			lines = append(lines, renderFacts(issues, "  ", 1)...)
		} else {
			lines = append(lines, "No problems found.")
		}
		if r, _ := out["remedy"].(map[string]any); r != nil {
			lines = append(lines, "", fmt.Sprintf("Remedy %v, up %v, %v conversations, %v memories, %v skills",
				r["version"], r["uptime"], r["chat_sessions_count"], r["memory_entries"], r["skills_count"]))
			lines = append(lines, fmt.Sprintf("Model: %v · %v", r["active_provider"], r["active_model"]))
		}
		if p, _ := out["providers"].(map[string]any); p != nil {
			lines = append(lines, fmt.Sprintf("Providers connected: %v", p["connected_count"]))
		}
		if c, _ := out["computer"].(map[string]any); c != nil {
			lines = append(lines, fmt.Sprintf("Computer use: host connected %v, %v job(s) waiting", c["host_connected"], c["pending_jobs"]))
		}
		return say("%s", strings.Join(lines, "\n"))

	case "/version":
		_, out := who.call(http.MethodGet, "/api/status", nil)
		return say("Remedy %v, running for %v.", out["version"], out["uptime"])

	case "/update", "/updates":
		_, out := who.call(http.MethodGet, "/api/updates/check", nil)
		if e, _ := out["error"].(string); strings.TrimSpace(e) != "" {
			return say("Could not check for updates: %s", e)
		}
		if avail, _ := out["update_available"].(bool); avail {
			return say("You are on %v. Version %v is out: %v\nThe desktop app installs it from its update notice.", out["current_version"], out["latest_desktop"], out["release_url"])
		}
		return say("You are on %v, which is the newest.", out["current_version"])

	case "/commands":
		return say("%s", slashCommandIndex())
	}
	return nil
}

// slashCommandIndex is every command under its group.
func slashCommandIndex() string {
	order := []string{"Conversation", "Model", "Memory", "Her work", "Settings", "This PC", "Help"}
	byGroup := map[string][]string{}
	for _, c := range allSlashCommands() {
		g, _ := c["group"].(string)
		if g == "" {
			g = "Conversation"
		}
		name, _ := c["name"].(string)
		desc, _ := c["description"].(string)
		byGroup[g] = append(byGroup[g], fmt.Sprintf("  %s — %s", name, desc))
	}
	var out []string
	for _, g := range order {
		if len(byGroup[g]) == 0 {
			continue
		}
		out = append(out, "**"+g+"**")
		out = append(out, byGroup[g]...)
		out = append(out, "")
	}
	return strings.TrimSpace(strings.Join(out, "\n"))
}

// slashCommandHelp is what /help <command> says about one command.
func slashCommandHelp(name string) (string, bool) {
	n := strings.ToLower(strings.TrimSpace(name))
	if !strings.HasPrefix(n, "/") {
		n = "/" + n
	}
	for _, c := range allSlashCommands() {
		cn, _ := c["name"].(string)
		aliases, _ := c["aliases"].([]string)
		hit := cn == n
		for _, a := range aliases {
			hit = hit || a == n
		}
		if !hit {
			continue
		}
		lines := []string{fmt.Sprintf("**%s** — %v", cn, c["description"])}
		if args, _ := c["arguments"].(string); args != "" {
			lines = append(lines, "  Takes: "+args)
		}
		if len(aliases) > 0 {
			lines = append(lines, "  Also: "+strings.Join(aliases, ", "))
		}
		return strings.Join(lines, "\n"), true
	}
	return "", false
}

func firstOf(row map[string]any, keys ...string) any {
	for _, k := range keys {
		if s, ok := row[k].(string); ok && strings.TrimSpace(s) != "" {
			return s
		}
	}
	return "(unnamed)"
}

// groupDigits writes a count with thin grouping: 1234567 -> 1,234,567.
func groupDigits(n float64) string {
	s := strconv.FormatInt(int64(n+0.5), 10)
	if len(s) <= 3 {
		return s
	}
	var b strings.Builder
	lead := len(s) % 3
	if lead > 0 {
		b.WriteString(s[:lead])
	}
	for i := lead; i < len(s); i += 3 {
		if b.Len() > 0 {
			b.WriteByte(',')
		}
		b.WriteString(s[i : i+3])
	}
	return b.String()
}

// forgetMemoryLIKE removes the notes whose title or text contains `text`.
func (s *sessionStore) forgetMemoryLIKE(text string) (int, error) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if err := s.ensurePartnerMemorySchema(); err != nil {
		return 0, err
	}
	like := likePattern(strings.TrimSpace(text))
	res, err := s.db.Exec(`DELETE FROM memory_entries WHERE title LIKE ? ESCAPE '\' OR content LIKE ? ESCAPE '\'`, like, like)
	if err != nil {
		return 0, err
	}
	n, _ := res.RowsAffected()
	return int(n), nil
}

// phoneSettingsLocked is the phone's own rule for a settings body, applied to
// a settings change that a command makes on the phone's behalf.
func phoneSettingsLocked(body any) string {
	raw, err := json.Marshal(body)
	if err != nil {
		return "bad settings body"
	}
	return connect.SettingsWriteLocked(raw)
}
