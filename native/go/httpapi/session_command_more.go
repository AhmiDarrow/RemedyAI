package httpapi

import (
	"bytes"
	"encoding/json"
	"fmt"
	"net/http"
	"net/url"
	"strings"
	"unicode/utf8"

	"github.com/AhmiDarrow/RemedyAI/native/go/connect"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/houserules"
)

// The slash commands that do something from the talk field, wherever it is: the
// desktop, the web page, a paired phone. Each one answers in words and, where
// there is something to do, does it here. None of them points at a panel or an
// API, because on a phone there is no panel to open.
//
// They are built on the runtime's own endpoints (selfCall), so a command and
// the button that does the same thing cannot drift apart.

// moreSlashCommands is listed by /help after the older ones.
var moreSlashCommands = []map[string]any{
	{"name": "/stop", "description": "Stop the reply that is being written in this conversation", "aliases": []string{"/abort"}, "arguments": nil},
	{"name": "/status", "description": "What this conversation is on and whether Remedy is working", "aliases": []string{}, "arguments": nil},
	{"name": "/think", "description": "Show or set how much she thinks before answering: /think off|low|medium|high", "aliases": []string{"/thinking"}, "arguments": "level"},
	{"name": "/search", "description": "Find earlier conversations by what was said: /search <words>", "aliases": []string{"/find"}, "arguments": "words"},
	{"name": "/rename", "description": "Give this conversation a title: /rename <title>", "aliases": []string{}, "arguments": "title"},
	{"name": "/remember", "description": "Save a fact to memory: /remember <text>", "aliases": []string{}, "arguments": "text"},
	{"name": "/goal", "description": "Add a goal: /goal <title>", "aliases": []string{}, "arguments": "title"},
	{"name": "/plan", "description": "Show the latest plan, or /plan approve | /plan cancel", "aliases": []string{}, "arguments": "approve|cancel"},
	{"name": "/approve", "description": "Say yes to what Remedy is waiting to do: /approve [id]", "aliases": []string{"/yes"}, "arguments": "id"},
	{"name": "/deny", "description": "Say no to what Remedy is waiting to do: /deny [id]", "aliases": []string{"/no"}, "arguments": "id"},
}

// memWriter collects a handler's answer for selfCall.
type memWriter struct {
	header http.Header
	status int
	body   bytes.Buffer
}

func (w *memWriter) Header() http.Header         { return w.header }
func (w *memWriter) WriteHeader(code int)        { w.status = code }
func (w *memWriter) Write(b []byte) (int, error) { return w.body.Write(b) }

// slashCaller is who typed the command. A paired phone may do through a
// command only what its panes let it do directly: a pane the owner turned off
// stays off, whichever way the phone asks.
type slashCaller struct {
	s     *Server
	phone bool
}

// allowed reports why the phone may not make this call, or "".
func (c slashCaller) allowed(method, path string) string {
	if !c.phone {
		return ""
	}
	query := ""
	if i := strings.Index(path, "?"); i >= 0 {
		path, query = path[:i], path[i+1:]
	}
	panes := connect.PanesFromConfig(map[string]any(LoadConfig(c.s.homeDir)))
	if why := connect.ConnectForbidden(method, path, query, panes); why != "" {
		return "That is turned off for the phone in Connect settings on the PC."
	}
	return ""
}

func (c slashCaller) call(method, path string, body any) (int, map[string]any) {
	if why := c.allowed(method, path); why != "" {
		return http.StatusForbidden, map[string]any{"detail": why}
	}
	// The phone's own list of settings it may never change applies here too.
	if c.phone && method == http.MethodPut && strings.HasPrefix(path, "/api/settings") && body != nil {
		if phoneSettingsLocked(body) != "" {
			return http.StatusForbidden, map[string]any{"detail": "That setting is changed at the PC, not from a phone."}
		}
	}
	return c.s.selfCall(method, path, body)
}

// selfCall runs one of the runtime's own endpoints in-process, as the owner,
// and returns its status and JSON object (nil when the body is not one).
func (s *Server) selfCall(method, path string, body any) (int, map[string]any) {
	var rd *bytes.Reader
	if body != nil {
		raw, err := json.Marshal(body)
		if err != nil {
			return http.StatusBadRequest, nil
		}
		rd = bytes.NewReader(raw)
	} else {
		rd = bytes.NewReader(nil)
	}
	req, err := http.NewRequest(method, "http://127.0.0.1"+path, rd)
	if err != nil {
		return http.StatusBadRequest, nil
	}
	req.Header.Set("Content-Type", "application/json")
	req.RemoteAddr = "127.0.0.1:0"
	w := &memWriter{header: http.Header{}, status: http.StatusOK}
	s.mux.ServeHTTP(w, req)
	var out map[string]any
	_ = json.Unmarshal(w.body.Bytes(), &out)
	return w.status, out
}

func detailOf(out map[string]any, fallback string) string {
	if d, _ := out["detail"].(string); strings.TrimSpace(d) != "" {
		return d
	}
	return fallback
}

// executeMoreSlash runs one of moreSlashCommands, or returns nil for another command.
func (s *Server) executeMoreSlash(sessionID, raw string, phone bool) map[string]any {
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
	needSession := func() map[string]any {
		if sessionID == "" {
			return say("Open a conversation first, then send %s.", name)
		}
		return nil
	}
	switch name {
	case "/stop", "/abort":
		if m := needSession(); m != nil {
			return m
		}
		if s.claims == nil || s.claims.ActiveRequest(sessionID) == "" {
			return say("Nothing is being written in this conversation right now.")
		}
		status, out := who.call(http.MethodPost, "/api/sessions/"+url.PathEscape(sessionID)+"/abort", map[string]any{})
		if status >= 300 {
			return say("Could not stop it: %s", detailOf(out, "the runtime refused"))
		}
		return map[string]any{"text": "Stopped. What was already done stays.", "action": "stopped"}

	case "/status":
		cfg := LoadConfig(s.homeDir)
		lines := []string{}
		if m := s.slashModel(sessionID, "", false); m != nil {
			if p, _ := m["provider"].(string); p != "" {
				lines = append(lines, fmt.Sprintf("Model: %s · %v", p, m["model"]))
			}
		}
		lines = append(lines,
			"Thinking: "+cfgString(cfg, "thinking_level", "high"),
			"Approvals: "+cfgString(cfg, "approval_mode", "ask"),
			"Access: "+cfgString(cfg, "access_scope", "project"),
		)
		working := sessionID != "" && s.claims != nil && s.claims.ActiveRequest(sessionID) != ""
		if working {
			lines = append(lines, "Remedy is writing a reply here. Send /stop to stop it.")
		} else {
			lines = append(lines, "Remedy is not working in this conversation.")
		}
		if n := len(who.pendingApprovals()); n > 0 {
			lines = append(lines, fmt.Sprintf("%d thing(s) wait for your yes. Send /approve or /deny.", n))
		}
		return say("%s", strings.Join(lines, "\n"))

	case "/think", "/thinking":
		cur := cfgString(LoadConfig(s.homeDir), "thinking_level", "high")
		want := strings.ToLower(arg)
		if want == "" {
			return say("Thinking is set to **%s**. Change it with /think off, low, medium or high.", cur)
		}
		switch want {
		case "off", "low", "medium", "high":
		default:
			return say("Thinking takes off, low, medium or high. It is **%s** now.", cur)
		}
		status, out := who.call(http.MethodPut, "/api/settings", map[string]any{"thinking_level": want})
		if status >= 300 {
			return say("Could not change it: %s", detailOf(out, "the runtime refused"))
		}
		return map[string]any{"text": "Thinking is now **" + want + "**.", "action": "settings_changed"}

	case "/search", "/find":
		if arg == "" {
			return say("Say what to look for: /search <words>.")
		}
		res, err := toolSessionsSearch(s, partnerToolInput{Query: arg})
		if err != nil {
			return say("%s", err.Error())
		}
		rows, _ := res.(map[string]any)["sessions"].([]map[string]any)
		if len(rows) == 0 {
			return say("No conversation mentions “%s”.", arg)
		}
		lines := []string{fmt.Sprintf("Conversations that mention “%s”:", arg)}
		for _, r := range rows {
			lines = append(lines, fmt.Sprintf("  **%v**", r["title"]))
			if snips, _ := r["snippets"].([]map[string]any); len(snips) > 0 {
				lines = append(lines, fmt.Sprintf("      %v", snips[0]["text"]))
			}
		}
		lines = append(lines, "", "Ask Remedy to carry one of them on, or open it from the list of sessions.")
		return map[string]any{"text": strings.Join(lines, "\n"), "sessions": rows}

	case "/rename":
		if m := needSession(); m != nil {
			return m
		}
		title := truncateRunes(arg, 120)
		if title == "" {
			return say("Give the title: /rename <title>.")
		}
		status, out := who.call(http.MethodPatch, "/api/sessions/"+url.PathEscape(sessionID), map[string]any{"title": title})
		if status >= 300 {
			return say("Could not rename it: %s", detailOf(out, "the runtime refused"))
		}
		return map[string]any{"text": "This conversation is now called “" + title + "”.", "action": "renamed", "title": title}

	case "/remember":
		if arg == "" {
			return say("Say what to remember: /remember <text>.")
		}
		if s.sessions == nil {
			return say("Memory is not available right now.")
		}
		if looksLikeSecret(arg) {
			return say("That looks like a key or a password. Memory is not the place for one, so it was not saved.")
		}
		// Typed by the owner as a command: theirs, and asked for.
		meta := map[string]any{"source": "explicit", "authority": "owner", "inferred": false, "why": "you asked to remember"}
		if sessionID != "" {
			meta["session_id"] = sessionID
		}
		if err := s.sessions.insertMemoryNote(truncateRunes(arg, 60), arg, sessionID, 0.8, meta); err != nil {
			return say("Could not save that: %s", err.Error())
		}
		if utf8.RuneCountInString(arg) < 400 {
			_, _ = s.sessions.upsertProfileFact("default", profileFactWrite{
				Text: arg, Category: "general", Source: "explicit", Authority: "owner",
				Why: "you asked to remember", SessionID: sessionID, Confidence: 0.9, Force: true,
			})
		}
		return say("Saved to memory: %s", truncateRunes(arg, 200))

	case "/goal":
		if arg == "" {
			return say("Give the goal a title: /goal <title>. Send /goals to see the open ones.")
		}
		status, out := who.call(http.MethodPost, "/api/goals", map[string]any{"title": truncateRunes(arg, 160)})
		if status >= 300 {
			return say("Could not add the goal: %s", detailOf(out, "the runtime refused"))
		}
		return map[string]any{"text": "Goal added: " + truncateRunes(arg, 160), "action": "goal_added"}

	case "/goals":
		_, out := who.call(http.MethodGet, "/api/goals", nil)
		list, _ := out["goals"].([]any)
		var lines []string
		for _, g := range list {
			row, _ := g.(map[string]any)
			status, _ := row["status"].(string)
			if status == "done" || status == "dropped" || status == "archived" {
				continue
			}
			line := fmt.Sprintf("  **%v**", row["title"])
			if next, _ := row["next_action"].(string); strings.TrimSpace(next) != "" {
				line += " — next: " + truncateRunes(next, 100)
			}
			lines = append(lines, line)
		}
		if len(lines) == 0 {
			return say("No open goals. Add one with /goal <title>.")
		}
		return say("Open goals:\n%s", strings.Join(lines, "\n"))

	case "/plans":
		path := "/api/plans?limit=10"
		if sessionID != "" {
			path += "&session_id=" + url.QueryEscape(sessionID)
		}
		_, out := who.call(http.MethodGet, path, nil)
		list, _ := out["plans"].([]any)
		if len(list) == 0 {
			return say("No plans in this conversation yet.")
		}
		lines := []string{"Plans:"}
		for _, p := range list {
			row, _ := p.(map[string]any)
			lines = append(lines, fmt.Sprintf("  **%v** (%v)", row["title"], row["status"]))
		}
		lines = append(lines, "", "Send /plan to see the latest one.")
		return say("%s", strings.Join(lines, "\n"))

	case "/plan":
		path := "/api/plans/latest"
		if sessionID != "" {
			path += "?session_id=" + url.QueryEscape(sessionID)
		}
		_, out := who.call(http.MethodGet, path, nil)
		plan, _ := out["plan"].(map[string]any)
		if plan == nil {
			return say("There is no plan in this conversation yet. Ask Remedy to plan something first.")
		}
		id, _ := plan["id"].(string)
		switch strings.ToLower(arg) {
		case "":
			md, _ := out["markdown"].(string)
			if strings.TrimSpace(md) == "" {
				md = fmt.Sprintf("**%v** (%v)", plan["title"], plan["status"])
			}
			return say("%s\n\nSend /plan approve to go ahead, or /plan cancel.", md)
		case "approve", "approved", "yes", "go":
			return who.planStatus(id, "approved", "Plan approved. Remedy can go ahead with it.")
		case "cancel", "cancelled", "no", "stop":
			return who.planStatus(id, "cancelled", "Plan cancelled.")
		default:
			return say("/plan takes approve or cancel. Send /plan alone to see it.")
		}

	case "/house-rules", "/houserules", "/house_rules":
		return s.slashHouseRules(arg, phone)

	case "/approve", "/yes":
		return who.resolve(arg, true)
	case "/deny", "/no":
		return who.resolve(arg, false)

	case "/whoami":
		cfg := LoadConfig(s.homeDir)
		who := strings.TrimSpace(cfgString(cfg, "user_name", ""))
		if who == "" {
			who = "the owner of this PC"
		}
		lines := []string{"You are " + who + ". Everything Remedy knows about you stays in her memory on this PC."}
		if s.sessions != nil {
			if rows, err := s.sessions.listRecentMemory(5); err == nil && len(rows) > 0 {
				lines = append(lines, "", "The latest things she remembers:")
				for _, r := range rows {
					lines = append(lines, "  "+truncateRunes(strings.TrimSpace(r.Title+" — "+r.Content), 140))
				}
			}
		}
		lines = append(lines, "", "Search it with /memory <words>, add to it with /remember <text>.")
		return say("%s", strings.Join(lines, "\n"))
	}
	return nil
}

func (who slashCaller) planStatus(planID, status, done string) map[string]any {
	if planID == "" {
		return map[string]any{"text": "That plan has no id; ask Remedy to plan again."}
	}
	code, out := who.call(http.MethodPost, "/api/plans/"+url.PathEscape(planID)+"/status", map[string]any{"status": status})
	if code >= 300 {
		return map[string]any{"text": "Could not change the plan: " + detailOf(out, "the runtime refused")}
	}
	return map[string]any{"text": done, "action": "plan_" + status}
}

func (who slashCaller) pendingApprovals() []map[string]any {
	_, out := who.call(http.MethodGet, "/api/approvals", nil)
	list, _ := out["approvals"].([]any)
	var pending []map[string]any
	for _, a := range list {
		row, _ := a.(map[string]any)
		if st, _ := row["status"].(string); st == "pending" {
			pending = append(pending, row)
		}
	}
	return pending
}

// slashResolve answers one waiting approval: the one named by the start of its
// id, or the only one waiting. A yes from here covers that one action only.
func (who slashCaller) resolve(arg string, approve bool) map[string]any {
	pending := who.pendingApprovals()
	if len(pending) == 0 {
		return map[string]any{"text": "Nothing is waiting for your answer."}
	}
	describe := func(row map[string]any) string {
		text, _ := row["summary"].(string)
		if strings.TrimSpace(text) == "" {
			text = fmt.Sprintf("%v: %v", row["tool"], row["command"])
		}
		id, _ := row["id"].(string)
		if len(id) > 8 {
			id = id[:8]
		}
		return fmt.Sprintf("  %s  %s", id, truncateRunes(strings.Join(strings.Fields(text), " "), 140))
	}
	var pick map[string]any
	want := strings.ToLower(strings.TrimSpace(arg))
	if want != "" {
		for _, row := range pending {
			if id, _ := row["id"].(string); strings.HasPrefix(strings.ToLower(id), want) {
				if pick != nil {
					return map[string]any{"text": "More than one waiting action starts with “" + arg + "”. Give more of the id."}
				}
				pick = row
			}
		}
		if pick == nil {
			return map[string]any{"text": "No waiting action starts with “" + arg + "”."}
		}
	} else if len(pending) == 1 {
		pick = pending[0]
	} else {
		lines := []string{fmt.Sprintf("%d actions are waiting. Say which one, by the start of its id:", len(pending))}
		for _, row := range pending {
			lines = append(lines, describe(row))
		}
		return map[string]any{"text": strings.Join(lines, "\n")}
	}
	id, _ := pick["id"].(string)
	code, out := who.call(http.MethodPost, "/api/approvals/"+url.PathEscape(id)+"/resolve", map[string]any{"approve": approve, "scope": "once"})
	if code >= 300 {
		return map[string]any{"text": "Could not record that: " + detailOf(out, "the runtime refused")}
	}
	word := "Approved, this once"
	if !approve {
		word = "Denied"
	}
	return map[string]any{"text": word + ":\n" + describe(pick), "action": "approval_resolved"}
}

// slashHouseRules shows the house rules, or reopens them with "revisit".
// Reopening starts the house-rules conversation again, so only the PC may do
// it; a paired phone can read the rules.
func (s *Server) slashHouseRules(arg string, phone bool) map[string]any {
	switch strings.ToLower(strings.TrimSpace(arg)) {
	case "again", "revisit", "redo", "review":
		if phone {
			return map[string]any{"text": "House rules can only be reopened on the PC. Send /house-rules to read them."}
		}
		out, _ := houserules.Dispatch(s.houseRulesEnv(), map[string]any{"action": "revisit", "caller_origin": "desktop"})
		if ok, _ := out["ok"].(bool); !ok {
			msg, _ := out["error"].(string)
			if strings.TrimSpace(msg) == "" {
				msg = "Could not reopen house rules."
			}
			return map[string]any{"text": msg}
		}
		text, _ := out["prompt"].(string)
		if strings.TrimSpace(text) == "" {
			text = "Let's go over house rules again."
		}
		if sess, _ := out["session"].(map[string]any); sess != nil {
			if sum, _ := sess["summary"].(string); strings.TrimSpace(sum) != "" {
				text += "\n\nCurrent rules:\n" + sum
			}
		}
		return map[string]any{"text": text}
	case "":
	default:
		return map[string]any{"text": "/house-rules takes revisit. Send /house-rules alone to see them."}
	}
	out, _ := houserules.Dispatch(s.houseRulesEnv(), map[string]any{"action": "summarize", "caller_origin": "desktop"})
	sum, _ := out["summary"].(string)
	if strings.TrimSpace(sum) == "" {
		sum = "Defaults are in effect."
	}
	return map[string]any{"text": "**House rules**\n\n" + sum +
		"\n\nSay \u201clet's go over house rules again\u201d anytime, or `/house-rules revisit`. " +
		"Change a setting in chat or in Settings. Skipped items keep their defaults."}
}
