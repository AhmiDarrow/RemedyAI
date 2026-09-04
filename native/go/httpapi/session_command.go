package httpapi

import (
	"fmt"
	"strings"
)

// Built-in slash command table (Desktop palette / POST .../command).
var builtinSlashCommands = []map[string]any{
	{"name": "/help", "description": "Show available commands", "aliases": []string{}, "arguments": nil},
	{"name": "/new", "description": "Create a new chat session", "aliases": []string{}, "arguments": nil},
	{"name": "/reset", "description": "Full reset of this session (history) — stay in the same tab", "aliases": []string{"/clear"}, "arguments": nil},
	{"name": "/sessions", "description": "List recent sessions", "aliases": []string{}, "arguments": nil},
	{"name": "/models", "description": "List available models", "aliases": []string{}, "arguments": nil},
	{"name": "/thinking", "description": "Toggle thinking visibility", "aliases": []string{}, "arguments": nil},
	{"name": "/memory", "description": "Search memory", "aliases": []string{}, "arguments": "query"},
	{"name": "/skills", "description": "List available skills", "aliases": []string{}, "arguments": nil},
	{"name": "/export", "description": "Export this session as a .txt file (desktop)", "aliases": []string{}, "arguments": nil},
	{"name": "/whoami", "description": "Show what Remedy knows about you", "aliases": []string{}, "arguments": nil},
	{"name": "/goals", "description": "List open goals", "aliases": []string{}, "arguments": nil},
	{"name": "/plans", "description": "List structured task plans", "aliases": []string{}, "arguments": nil},
	{"name": "/security-status", "description": "Show partner control settings", "aliases": []string{"/security", "/secstatus"}, "arguments": nil},
}

func (s *Server) executeSlashCommand(sessionID, command string) map[string]any {
	raw := strings.TrimSpace(command)
	stripped := strings.ToLower(raw)

	if stripped == "/help" || stripped == "/h" {
		var lines []string
		for _, c := range builtinSlashCommands {
			name, _ := c["name"].(string)
			desc, _ := c["description"].(string)
			lines = append(lines, fmt.Sprintf("  %s — %s", name, desc))
		}
		keys := "**Keyboard shortcuts**\n" +
			"  Enter — Send message (composer)\n" +
			"  Shift+Enter — New line (composer)\n" +
			"  Ctrl+N — New chat session\n" +
			"  Ctrl+P / Ctrl+K — Command palette\n" +
			"  Ctrl+B — Toggle plan mode\n" +
			"  Ctrl+, — Settings\n" +
			"  Ctrl+/ or F1 — Full Help wiki (owner's manual)\n" +
			"  Escape — Close panels, Help, and palette\n"
		tips := "\n**Tips**\n" +
			"  · **F1** opens the offline Help wiki (searchable owner's manual).\n" +
			"  · Connect a provider in Settings to chat with models.\n" +
			"  · Plan mode explores without changing files; Build mode can edit.\n" +
			"  · Type @ to reference project files.\n" +
			"  · Your data stays in ~/.remedy on this machine.\n"
		return map[string]any{
			"text": "**Slash commands**\n" + strings.Join(lines, "\n") + "\n\n" + keys + tips,
		}
	}

	if stripped == "/new" || stripped == "/n" {
		return map[string]any{"text": "Session marked for creation.", "action": "new_session"}
	}

	if stripped == "/reset" || stripped == "/clear" {
		if sessionID == "" {
			return map[string]any{"text": "No active session to reset. Open a chat first, or use `/new`."}
		}
		if s.sessions == nil {
			return map[string]any{"text": "Memory store not available; cannot reset session."}
		}
		n, err := s.sessions.ClearMessages(sessionID)
		if err != nil {
			return map[string]any{"text": fmt.Sprintf("Could not reset session: %v", err)}
		}
		msg := "message"
		if n != 1 {
			msg = "messages"
		}
		return map[string]any{
			"text": fmt.Sprintf(
				"Session fully reset (%d %s). Same session tab — history cleared. "+
					"Durable memory (/remember) kept. Send a message to start as if new.",
				n, msg,
			),
			"action":  "reset_session",
			"cleared": n,
			"stats":   map[string]any{"ok": true, "messages": n},
		}
	}

	if stripped == "/sessions" || stripped == "/s" {
		if s.sessions == nil {
			return map[string]any{"text": "Memory store not available."}
		}
		list, err := s.sessions.List(10, 0)
		if err != nil {
			return map[string]any{"text": fmt.Sprintf("Could not list sessions: %v", err)}
		}
		if len(list) == 0 {
			return map[string]any{"text": "No sessions found."}
		}
		lines := make([]string, 0, len(list))
		for _, sess := range list {
			idShort := sess.ID
			if len(idShort) > 8 {
				idShort = idShort[:8]
			}
			lines = append(lines, fmt.Sprintf("  %s — %d msg — %s", sess.Title, sess.MessageCount, idShort))
		}
		return map[string]any{"text": "Recent sessions:\n" + strings.Join(lines, "\n")}
	}

	if stripped == "/models" || stripped == "/m" {
		return map[string]any{
			"text": "Model list is filtered by your configured provider. " +
				"Use the model picker in the status bar, or GET /api/models.",
			"action": "list_models",
		}
	}

	if stripped == "/thinking" {
		return map[string]any{"text": "Thinking visibility toggled."}
	}

	if stripped == "/memory" || stripped == "/mem" {
		return map[string]any{"text": "Usage: /memory <query>"}
	}
	if strings.HasPrefix(stripped, "/memory ") {
		query := strings.TrimSpace(raw[len("/memory "):])
		if query == "" || s.sessions == nil {
			return map[string]any{"text": "Usage: /memory <query>"}
		}
		entries, err := s.sessions.searchMemoryLIKE(query, 5)
		if err != nil || len(entries) == 0 {
			return map[string]any{"text": "No memory entries found."}
		}
		lines := make([]string, 0, len(entries))
		for _, e := range entries {
			content := e.Content
			if len(content) > 120 {
				content = content[:120]
			}
			lines = append(lines, fmt.Sprintf("  **%s** — %s", e.Title, content))
		}
		return map[string]any{"text": "Memory results:\n" + strings.Join(lines, "\n")}
	}

	if stripped == "/skills" || stripped == "/sk" || strings.HasPrefix(stripped, "/skills ") {
		skills := s.discoverSkills()
		if len(skills) == 0 {
			return map[string]any{
				"text": "No skills loaded yet. Default skills ship with Remedy — restart the server " +
					"to discover bundled skills, or drop SKILL.md packages into `~/.remedy/skills/`.\n\n" +
					"**Built-in tools:** `file_read`, `file_write`, `list_dir`, `bash_exec`.",
			}
		}
		limit := 40
		if len(skills) < limit {
			limit = len(skills)
		}
		lines := make([]string, 0, limit)
		for i := 0; i < limit; i++ {
			rec := skills[i]
			desc := rec.Description
			if len(desc) > 80 {
				desc = desc[:77] + "…"
			}
			lines = append(lines, fmt.Sprintf("  · **%s** — %s", rec.Name, desc))
		}
		toolsHint := "\n\n**Built-in tools** (always available): " +
			"`file_read`, `file_write`, `list_dir`, `bash_exec`.\n" +
			"Skills are procedure packs the agent follows; tools are executable actions."
		return map[string]any{
			"text": fmt.Sprintf("**%d skills loaded:**\n", len(skills)) + strings.Join(lines, "\n") + toolsHint,
		}
	}

	if stripped == "/export" || stripped == "/export-session" {
		return map[string]any{
			"text": "Export this chat from the session menu, or:\n" +
				"• API: `GET /api/sessions/{id}/export?format=txt`",
			"action": "export_session",
		}
	}

	if stripped == "/whoami" {
		return map[string]any{
			"text": "You're the owner of this local Remedy. Profile facts live in Memory — " +
				"use `/memory <query>` or the Memory panel.",
		}
	}

	if stripped == "/goals" {
		return map[string]any{
			"text":   "Open goals are listed in the Goals panel, or GET /api/goals.",
			"action": "list_goals",
		}
	}

	if stripped == "/plans" {
		return map[string]any{
			"text":   "Plans are listed in the Plans panel, or GET /api/plans.",
			"action": "list_plans",
		}
	}

	if stripped == "/security-status" || stripped == "/security" || stripped == "/secstatus" {
		cfg := LoadConfig(s.homeDir)
		mode := cfgString(cfg, "approval_mode", "ask")
		scope := cfgString(cfg, "access_scope", "project")
		return map[string]any{
			"text": fmt.Sprintf(
				"**Security status**\n  approval_mode: %s\n  access_scope: %s\n  home: %s",
				mode, scope, ResolveHomeDir(s.homeDir),
			),
		}
	}

	return map[string]any{
		"text": fmt.Sprintf("Unknown command: %s\nType `/help` for available commands.", raw),
	}
}
