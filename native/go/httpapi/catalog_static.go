package httpapi

import "net/http"

// Builtin slash commands — lockstep with Python slash_commands._BUILTIN_COMMANDS.
var builtinCommands = []map[string]any{
	{"name": "/help", "description": "Show available commands", "aliases": []string{}, "arguments": nil},
	{"name": "/new", "description": "Create a new chat session", "aliases": []string{}, "arguments": nil},
	{
		"name": "/reset",
		"description": "Full reset of this session (history, plans, brief, attachments) " +
			"— stay in the same tab; like a new chat without switching",
		"aliases":   []string{"/clear"},
		"arguments": nil,
	},
	{"name": "/sessions", "description": "List recent sessions", "aliases": []string{}, "arguments": nil},
	{"name": "/compact", "description": "Memory Harness: compress session into Session Brief", "aliases": []string{}, "arguments": "focus"},
	{"name": "/harness", "description": "Show Memory Harness Session Brief / stats", "aliases": []string{}, "arguments": nil},
	{"name": "/models", "description": "List available models", "aliases": []string{}, "arguments": nil},
	{"name": "/thinking", "description": "Toggle thinking visibility", "aliases": []string{}, "arguments": nil},
	{"name": "/memory", "description": "Search memory", "aliases": []string{}, "arguments": "query"},
	{"name": "/remember", "description": "Save a durable fact to memory", "aliases": []string{}, "arguments": "text"},
	{"name": "/forget", "description": "Remove a remembered fact: /forget <text>", "aliases": []string{}, "arguments": "text"},
	{"name": "/pin", "description": "Pin a fact so it always injects: /pin <text>", "aliases": []string{}, "arguments": "text"},
	{"name": "/whoami", "description": "Show what Remedy knows about you", "aliases": []string{}, "arguments": nil},
	{
		"name":        "/stretch",
		"description": "Map this PC (hardware, tools, rooms) — first-home census",
		"aliases":     []string{"/home"},
		"arguments":   nil,
	},
	{"name": "/goals", "description": "List open goals", "aliases": []string{}, "arguments": nil},
	{"name": "/goal", "description": "Add a goal: /goal <title>", "aliases": []string{}, "arguments": "title"},
	{"name": "/plans", "description": "List structured task plans", "aliases": []string{}, "arguments": nil},
	{
		"name":        "/plan",
		"description": "Show latest plan, or /plan approve|cancel|new <title>",
		"aliases":     []string{},
		"arguments":   "approve|new <title>",
	},
	{"name": "/approve", "description": "Approve a pending high-impact action", "aliases": []string{}, "arguments": "id"},
	{"name": "/deny", "description": "Deny a pending high-impact action", "aliases": []string{}, "arguments": "id"},
	{"name": "/import", "description": "Import a folder of notes into memory", "aliases": []string{}, "arguments": "path"},
	{"name": "/export", "description": "Export this session as a .txt file (desktop)", "aliases": []string{}, "arguments": nil},
	{
		"name":        "/import-session",
		"description": "Import a session from .txt/.md (path or desktop file picker)",
		"aliases":     []string{"/session-import"},
		"arguments":   "path",
	},
	{"name": "/skills", "description": "List available skills", "aliases": []string{}, "arguments": nil},
	{
		"name":        "/helper",
		"description": "Offline help tips, or /helper error <text>",
		"aliases":     []string{"/tip"},
		"arguments":   "topic | error <text>",
	},
	{"name": "/handoff", "description": "List handoff notes", "aliases": []string{}, "arguments": nil},
	{
		"name":        "/security-status",
		"description": "Show partner control settings (approval mode, web tools, timeouts)",
		"aliases":     []string{"/security", "/secstatus"},
		"arguments":   nil,
	},
	{"name": "/init", "description": "Scan the project and generate AGENTS.md", "aliases": []string{}, "arguments": "path"},
}

// Builtin agents — lockstep with Python slash_commands._BUILTIN_AGENTS.
var builtinAgents = []map[string]any{
	{"name": "default", "description": "Remedy — general-purpose agent", "build_mode": true},
	{"name": "remedy", "description": "Remedy — meta-orchestrator with skill routing", "build_mode": true},
	{"name": "explore", "description": "Codebase explorer for search and analysis", "build_mode": false},
	{"name": "general", "description": "General-purpose agent for complex tasks", "build_mode": true},
}

func (s *Server) handleListAgents(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"agents": builtinAgents})
}

func (s *Server) handleListCommands(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]any{"commands": builtinCommands})
}
