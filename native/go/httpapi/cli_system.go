package httpapi

// Exported helpers for the SYSTEM group of the Go CLI (native/go/cli):
// `remedy setup` (config write + secret store), `remedy mcp serve` (stdio and
// Streamable HTTP MCP on the same dispatch the runtime serves at POST /mcp),
// `remedy uninstall` (vision wipe) and `remedy computer` (the CLI computer
// host on the filesystem job queue).

import (
	"bufio"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"net"
	"net/http"
	"os"
	"path/filepath"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// ---- remedy setup ------------------------------------------------------------

// sysSetupSecretFields are messenger_settings.SECRET_FIELD_KEYS.
var sysSetupSecretFields = []string{
	"bot_token", "access_token", "refresh_token", "oauth_client_secret",
	"app_token", "app_password", "app_secret", "verify_token", "signing_secret",
}

// sysInferKeyOwner is config.infer_key_owner: an unambiguous key prefix.
func sysInferKeyOwner(key string) string {
	k := strings.TrimSpace(key)
	switch {
	case k == "":
		return ""
	case strings.HasPrefix(k, "xai-") || (strings.HasPrefix(k, "eyJ") && strings.Count(k, ".") >= 2):
		return "xai"
	case strings.HasPrefix(k, "sk-ant"):
		return "anthropic"
	case strings.HasPrefix(k, "gsk_"):
		return "groq"
	case strings.HasPrefix(k, "AIza"):
		return "google"
	case strings.HasPrefix(k, "sk-or-"):
		return "openrouter"
	case strings.HasPrefix(k, "pplx-"):
		return "perplexity"
	}
	return ""
}

// sysInferKeyProvider is config.infer_key_provider (migration fallback).
func sysInferKeyProvider(key string) string {
	if o := sysInferKeyOwner(key); o != "" {
		return o
	}
	if strings.HasPrefix(strings.TrimSpace(key), "sk-") {
		return "deepseek"
	}
	return ""
}

func sysLooksLikeXai(key string) bool {
	k := strings.TrimSpace(key)
	return strings.HasPrefix(k, "xai-") || (strings.HasPrefix(k, "eyJ") && strings.Count(k, ".") >= 2)
}

// sysLLMKeyOwner is the owner migrate_secrets_from_config gives a legacy
// llm_api_key.
func sysLLMKeyOwner(cfg map[string]any, key string) string {
	provider := strings.ToLower(strings.TrimSpace(fmtString(cfg["llm_provider"])))
	last := strings.ToLower(strings.TrimSpace(fmtString(cfg["last_llm_provider"])))
	var owner string
	switch {
	case provider == "xai" && !sysLooksLikeXai(key):
		if last != "" && last != "xai" {
			owner = last
		} else if owner = sysInferKeyProvider(key); owner == "" {
			owner = "deepseek"
		}
	case provider != "":
		owner = provider
	default:
		if owner = sysInferKeyProvider(key); owner == "" {
			owner = "custom"
		}
	}
	if owner == "xai" && !sysLooksLikeXai(key) {
		if owner = sysInferKeyProvider(key); owner == "" {
			owner = "deepseek"
		}
	}
	return owner
}

// SetupWriteConfig is wizard._write_config: it moves every credential in cfg
// into the secret store (provider_keys, llm_api_key, messenger bot tokens),
// writes the rest to <home>/config.toml with scalars before tables, and
// returns the path. home is used when cfg has no home_dir.
func SetupWriteConfig(home string, cfg map[string]any) (string, error) {
	safe := map[string]any{}
	for k, v := range cfg {
		safe[k] = v
	}
	if raw := strings.TrimSpace(fmtString(safe["home_dir"])); raw != "" {
		home = raw
	}
	if strings.HasPrefix(home, "~") {
		if uh, err := os.UserHomeDir(); err == nil {
			home = filepath.Join(uh, home[1:])
		}
	}
	if strings.TrimSpace(home) == "" {
		return "", errors.New("setup: no home directory")
	}
	if err := os.MkdirAll(home, 0o700); err != nil {
		return "", err
	}
	path := filepath.Join(home, "config.toml")

	if table, ok := asStringMap(safe["provider_keys"]); ok {
		for k, v := range table {
			pk := strings.ToLower(strings.TrimSpace(k))
			val := strings.TrimSpace(fmtString(v))
			if pk != "" && val != "" {
				if err := secret.SetProviderSecret(home, pk, val); err != nil {
					return "", fmt.Errorf("store %s key: %w", pk, err)
				}
			}
		}
	}
	if key := strings.TrimSpace(fmtString(safe["llm_api_key"])); key != "" {
		owner := sysLLMKeyOwner(safe, key)
		if strings.TrimSpace(secret.GetProviderSecret(home, owner)) == "" {
			if err := secret.SetProviderSecret(home, owner, key); err != nil {
				return "", fmt.Errorf("store %s key: %w", owner, err)
			}
		}
	}
	for _, m := range messengerCatalog {
		sec, ok := asStringMap(safe[m.ID])
		if !ok {
			continue
		}
		for _, field := range sysSetupSecretFields {
			if val := strings.TrimSpace(fmtString(sec[field])); val != "" {
				if err := secret.SetProviderSecret(home, "ch:"+m.ID+":"+field, val); err != nil {
					return "", fmt.Errorf("store %s %s: %w", m.ID, field, err)
				}
			}
		}
	}
	delete(safe, "provider_keys")
	if _, ok := safe["llm_api_key"]; ok {
		safe["llm_api_key"] = ""
	}
	safe["secrets_store"] = "auth/provider_keys.json"
	for _, key := range []string{"home_dir", "project_path", "memory_db_path"} {
		if s := fmtString(safe[key]); strings.TrimSpace(s) != "" {
			safe[key] = strings.ReplaceAll(s, `\`, "/")
		}
	}
	if _, ok := safe["setup_completed"]; !ok {
		safe["setup_completed"] = true
	}
	if err := WriteConfig(path, ConfigMap(safe)); err != nil {
		return "", err
	}
	return path, nil
}

// ---- remedy mcp serve ----------------------------------------------------------

// MCP protocol revisions Python's mcp_talk accepts.
var sysMCPVersions = []string{"2024-11-05", "2025-03-26", "2025-11-25", "2026-07-28"}

const sysMCPLatest = "2026-07-28"

// sysNegotiateMCP is mcp_talk.negotiate_version.
func sysNegotiateMCP(requested string) string {
	for _, v := range sysMCPVersions {
		if requested == v {
			return v
		}
	}
	return sysMCPLatest
}

// newInteropOnlyServer is a Server with just what the interop (A2A + MCP)
// handlers touch: files under <home>/interop, the config and the plans dir.
// No session store, event bus or scheduler: `remedy mcp serve` must never run
// jobs beside a live runtime.
func newInteropOnlyServer(home, version string) *Server {
	if version == "" {
		version = Version
	}
	return &Server{
		started:     time.Now(),
		version:     version,
		homeDir:     home,
		mux:         http.NewServeMux(),
		interopLive: newInteropLiveCtl(),
	}
}

// MCPHost is the stdio MCP server (tools/mcp_server.py RemedyMCPServer): the
// skill export tools, plan tools, and the runtime's talk tools.
type MCPHost struct {
	s        *Server
	home     string
	skillsMu sync.Mutex
	skills   []skillRecord
	loaded   bool
	// RunScript runs a jailed skill script; replaceable in tests. It returns
	// combined output, exit code and an error message ("" when it ran).
	RunScript func(ctx context.Context, script string, args []string) (out string, code int, errMsg string)
}

// NewMCPHost makes a stdio MCP host for home.
func NewMCPHost(home, version string) *MCPHost {
	return &MCPHost{s: newInteropOnlyServer(home, version), home: ResolveHomeDir(home)}
}

// SetSkillRoots replaces skill discovery with exactly these roots (tests).
func (h *MCPHost) SetSkillRoots(roots ...string) {
	h.skillsMu.Lock()
	defer h.skillsMu.Unlock()
	h.skills = sysLoadSkills(roots)
	h.loaded = true
}

// sysLoadSkills reads skills from roots; a later root wins on a name clash.
func sysLoadSkills(roots []string) []skillRecord {
	byName := map[string]skillRecord{}
	for _, root := range roots {
		for _, rec := range loadSkillsFromRoot(root) {
			byName[rec.Name] = applyDefaultSkillStatus(rec)
		}
	}
	out := make([]skillRecord, 0, len(byName))
	for _, rec := range byName {
		out = append(out, rec)
	}
	sort.Slice(out, func(i, j int) bool { return strings.ToLower(out[i].Name) < strings.ToLower(out[j].Name) })
	return out
}

// registry is _load_registry: <home>/skills plus the bundled packs. It only
// reads; it never seeds the user skills folder.
func (h *MCPHost) registry() []skillRecord {
	h.skillsMu.Lock()
	defer h.skillsMu.Unlock()
	if !h.loaded {
		roots := skillSeedRoots(h.home)
		roots = append(roots, filepath.Join(h.home, "skills"))
		h.skills = sysLoadSkills(roots)
		h.loaded = true
	}
	return h.skills
}

func sysSkillTools() []map[string]any {
	obj := func(props map[string]any, required ...string) map[string]any {
		m := map[string]any{"type": "object", "properties": props}
		if len(required) > 0 {
			m["required"] = required
		}
		return m
	}
	return []map[string]any{
		{"name": "remedy_skill_list", "description": "List Remedy skills available on this machine (name, status, description). Learned + bundled packs.",
			"inputSchema": obj(map[string]any{
				"include_disabled": map[string]any{"type": "boolean", "description": "Include disabled/deprecated skills", "default": false},
				"limit":            map[string]any{"type": "integer", "default": 50},
			})},
		{"name": "remedy_skill_search", "description": "Search Remedy skills by query (ranked catalog).",
			"inputSchema": obj(map[string]any{
				"query": map[string]any{"type": "string"},
				"limit": map[string]any{"type": "integer", "default": 10},
			}, "query")},
		{"name": "remedy_skill_get", "description": "Load full SKILL.md instructions for a Trusted skill. Quarantined packs are refused until the owner Trusts them in Remedy.",
			"inputSchema": obj(map[string]any{"name": map[string]any{"type": "string", "description": "Skill name"}}, "name")},
		{"name": "remedy_skill_run", "description": "Run a skill script (scripts/). Blocked for quarantined skills. Requires REMEDY_MCP_ALLOW_RUN=1 in the environment (owner opt-in).",
			"inputSchema": obj(map[string]any{
				"name":   map[string]any{"type": "string"},
				"script": map[string]any{"type": "string", "description": "Script filename"},
				"args":   map[string]any{"type": "string", "description": "Optional args string"},
			}, "name")},
		{"name": "remedy_plan_list", "description": "List structured task plans saved by Remedy Plan mode.",
			"inputSchema": obj(map[string]any{"limit": map[string]any{"type": "integer", "default": 10}})},
		{"name": "remedy_plan_show", "description": "Show a plan by id, or the latest plan.",
			"inputSchema": obj(map[string]any{"plan_id": map[string]any{"type": "string"}})},
	}
}

// MCPToolNames lists what tools/list offers over stdio, in order.
func (h *MCPHost) MCPToolNames() []string {
	var out []string
	for _, t := range h.toolList() {
		out = append(out, fmtString(t["name"]))
	}
	return out
}

func (h *MCPHost) toolList() []map[string]any {
	return append(sysSkillTools(), interopMCPTools()...)
}

var sysTalkTools = map[string]bool{"remedy_ask": true, "remedy_delegate": true, "remedy_task_status": true, "remedy_share_context": true}

// Handle answers one decoded JSON-RPC message; nil means no reply
// (a notification).
func (h *MCPHost) Handle(ctx context.Context, message any) map[string]any {
	msg, ok := message.(map[string]any)
	if !ok {
		return rpcErr(nil, -32600, "Invalid Request")
	}
	id := msg["id"]
	method, _ := msg["method"].(string)
	params, _ := msg["params"].(map[string]any)
	if params == nil {
		params = map[string]any{}
	}
	if id == nil && method != "" {
		return nil
	}
	switch method {
	case "initialize":
		requested, _ := params["protocolVersion"].(string)
		if requested == "" {
			requested = "2024-11-05"
		}
		return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{
			"protocolVersion": sysNegotiateMCP(requested),
			"capabilities":    map[string]any{"tools": map[string]any{}},
			"serverInfo":      map[string]any{"name": "remedy", "version": h.s.version},
		}}
	case "tools/list":
		return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{"tools": h.toolList()}}
	case "tools/call":
		name, _ := params["name"].(string)
		args, _ := params["arguments"].(map[string]any)
		if args == nil {
			args = map[string]any{}
		}
		text, err := h.callTool(ctx, name, args)
		if err != nil {
			return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{
				"content": []map[string]any{{"type": "text", "text": "Error: " + err.Error()}},
				"isError": true,
			}}
		}
		return map[string]any{"jsonrpc": "2.0", "id": id, "result": map[string]any{
			"content": []map[string]any{{"type": "text", "text": text}},
			"isError": false,
		}}
	}
	// ping, resources/list, prompts/list and unknown methods: the runtime's
	// own MCP dispatch, so stdio and HTTP answer them identically.
	return h.s.dispatchMCP(ctx, id, method, msg)
}

func sysArgInt(args map[string]any, key string, def, lo, hi int) int {
	n := def
	switch v := args[key].(type) {
	case float64:
		if v != 0 {
			n = int(v)
		}
	case string:
		if p, err := strconv.Atoi(strings.TrimSpace(v)); err == nil && p != 0 {
			n = p
		}
	}
	return max(lo, min(n, hi))
}

func (h *MCPHost) findSkill(name string) (skillRecord, bool) {
	for _, rec := range h.registry() {
		if rec.Name == name {
			return rec, true
		}
	}
	return skillRecord{}, false
}

func (h *MCPHost) callTool(ctx context.Context, name string, args map[string]any) (string, error) {
	if sysTalkTools[name] {
		return h.s.callInteropMCPTool(ctx, name, args)
	}
	switch name {
	case "remedy_skill_list":
		include, _ := args["include_disabled"].(bool)
		limit := sysArgInt(args, "limit", 50, 1, 200)
		skills := h.registry()
		if len(skills) > limit*2 {
			skills = skills[:limit*2]
		}
		var lines []string
		for _, s := range skills {
			if !include && (s.Status == "disabled" || s.Status == "deprecated") {
				continue
			}
			q := ""
			if s.Quarantine {
				q = " [quarantine]"
			}
			lines = append(lines, fmt.Sprintf("- %s (%s)%s: %s", s.Name, s.Status, q, s.Description))
			if len(lines) >= limit {
				break
			}
		}
		if len(lines) == 0 {
			return "Skills:\n(none)", nil
		}
		return "Skills:\n" + strings.Join(lines, "\n"), nil
	case "remedy_skill_search":
		q := strings.TrimSpace(fmtString(args["query"]))
		limit := sysArgInt(args, "limit", 10, 1, 30)
		if q == "" {
			return "Provide query.", nil
		}
		ranked := rankSkills(h.registry(), q, limit, "")
		if len(ranked) == 0 {
			return "No skills matched: " + q, nil
		}
		lines := make([]string, 0, len(ranked))
		for _, r := range ranked {
			lines = append(lines, fmt.Sprintf("- %s (score=%.2f): %s", r.rec.Name, r.score, r.rec.Description))
		}
		return "Matches:\n" + strings.Join(lines, "\n"), nil
	case "remedy_skill_get":
		nm := strings.TrimSpace(fmtString(args["name"]))
		if nm == "" {
			return "Provide name.", nil
		}
		rec, ok := h.findSkill(nm)
		if !ok {
			var hits []string
			for _, r := range rankSkills(h.registry(), nm, 5, "") {
				hits = append(hits, r.rec.Name)
			}
			hint := strings.Join(hits, ", ")
			if hint == "" {
				hint = "none"
			}
			return fmt.Sprintf("Skill not found: %s. Closest: %s", nm, hint), nil
		}
		if rec.Quarantine {
			return fmt.Sprintf("Skill '%s' is quarantined. Trust it in Remedy Desktop (Skills panel) before external apps can read the full body.", nm), nil
		}
		return "# " + rec.Name + "\n\n" + skillBody(rec, false), nil
	case "remedy_skill_run":
		return h.skillRun(ctx, args), nil
	case "remedy_plan_list":
		limit := sysArgInt(args, "limit", 10, 1, 30)
		plans := h.s.listPlans("", limit)
		if len(plans) == 0 {
			return "No plans saved.", nil
		}
		lines := make([]string, 0, len(plans))
		for _, p := range plans {
			lines = append(lines, fmt.Sprintf("- [%s] %s (id=%s, steps=%d)", p.Status, p.Title, p.ID, len(p.Steps)))
		}
		return "Plans:\n" + strings.Join(lines, "\n"), nil
	case "remedy_plan_show":
		pid := strings.TrimSpace(fmtString(args["plan_id"]))
		var plan *taskPlan
		if pid != "" {
			plan = h.s.getPlan(pid)
		}
		if plan == nil {
			if plans := h.s.listPlans("", 1); len(plans) > 0 {
				plan = plans[0]
			}
		}
		if plan == nil {
			return "No plan found.", nil
		}
		return plan.summaryMarkdown(), nil
	}
	return "Unknown tool: " + name, nil
}

// ErrSkillScriptJail is a script name that is absolute or leaves scripts/.
var ErrSkillScriptJail = errors.New("script path escapes scripts/")

func sysAbsoluteScriptName(s string) bool {
	s = strings.TrimSpace(s)
	if s == "" {
		return false
	}
	if filepath.IsAbs(s) || strings.HasPrefix(s, "/") || strings.HasPrefix(s, `\`) {
		return true
	}
	return len(s) >= 2 && s[1] == ':'
}

// ResolveJailedSkillScript is skills/script_path.resolve_jailed_skill_script:
// script resolved under <skillDir>/scripts, never outside it (links
// included).
func ResolveJailedSkillScript(skillDir, script string) (string, error) {
	raw := strings.TrimSpace(script)
	if raw == "" {
		return "", fmt.Errorf("%w: empty script name", ErrSkillScriptJail)
	}
	if sysAbsoluteScriptName(raw) {
		return "", fmt.Errorf("%w: absolute script path rejected", ErrSkillScriptJail)
	}
	var parts []string
	for _, p := range strings.Split(strings.ReplaceAll(raw, `\`, "/"), "/") {
		if p != "" && p != "." {
			parts = append(parts, p)
		}
	}
	if len(parts) > 0 && parts[0] == "scripts" {
		parts = parts[1:]
	}
	if len(parts) == 0 {
		return "", ErrSkillScriptJail
	}
	for _, p := range parts {
		if p == ".." {
			return "", ErrSkillScriptJail
		}
	}
	rel := strings.Join(parts, "/")
	if sysAbsoluteScriptName(rel) {
		return "", fmt.Errorf("%w: absolute script path rejected", ErrSkillScriptJail)
	}
	base, err := filepath.Abs(skillDir)
	if err != nil {
		return "", err
	}
	root := filepath.Join(base, "scripts")
	if real, err := filepath.EvalSymlinks(root); err == nil {
		root = real
	}
	cand := filepath.Join(root, filepath.FromSlash(rel))
	if real, err := filepath.EvalSymlinks(cand); err == nil {
		cand = real
	}
	inside, err := filepath.Rel(root, cand)
	if err != nil || inside == ".." || strings.HasPrefix(inside, ".."+string(filepath.Separator)) || filepath.IsAbs(inside) {
		return "", ErrSkillScriptJail
	}
	return cand, nil
}

// sysSplitArgs is shlex.split (posix outside Windows): quotes group, a
// backslash escapes outside single quotes.
func sysSplitArgs(s string, posix bool) []string {
	var out []string
	var cur strings.Builder
	inWord := false
	var quote rune
	rs := []rune(s)
	for i := 0; i < len(rs); i++ {
		r := rs[i]
		switch {
		case quote != 0:
			if r == quote {
				quote = 0
			} else if r == '\\' && posix && quote == '"' && i+1 < len(rs) && (rs[i+1] == '"' || rs[i+1] == '\\') {
				i++
				cur.WriteRune(rs[i])
			} else {
				cur.WriteRune(r)
			}
		case r == '"' || r == '\'':
			quote = r
			inWord = true
		case r == '\\' && posix && i+1 < len(rs):
			i++
			cur.WriteRune(rs[i])
			inWord = true
		case r == ' ' || r == '\t' || r == '\n' || r == '\r':
			if inWord {
				out = append(out, cur.String())
				cur.Reset()
				inWord = false
			}
		default:
			cur.WriteRune(r)
			inWord = true
		}
	}
	if inWord {
		out = append(out, cur.String())
	}
	return out
}

func (h *MCPHost) skillRun(ctx context.Context, args map[string]any) string {
	switch strings.TrimSpace(os.Getenv("REMEDY_MCP_ALLOW_RUN")) {
	case "1", "true", "yes":
	default:
		return "skill_run disabled over MCP by default. Set REMEDY_MCP_ALLOW_RUN=1 to allow script execution (owner opt-in; still blocks quarantine)."
	}
	nm := strings.TrimSpace(fmtString(args["name"]))
	script := strings.TrimSpace(fmtString(args["script"]))
	rec, ok := h.findSkill(nm)
	if nm == "" || !ok {
		return "Skill not found: " + nm
	}
	if rec.Quarantine {
		return fmt.Sprintf("Skill '%s' is quarantined — Trust in Remedy first.", nm)
	}
	scripts := listSkillSubfiles(rec.Dir, "scripts")
	if len(scripts) == 0 && script == "" {
		return fmt.Sprintf("Skill '%s' has no scripts/ to run.", nm)
	}
	if rec.Dir == "" {
		return fmt.Sprintf("Skill '%s' has no on-disk path for scripts.", nm)
	}
	name := script
	if name == "" {
		name = scripts[0]
	}
	path, err := ResolveJailedSkillScript(rec.Dir, name)
	if err != nil {
		return "Script path escapes skill scripts/ directory"
	}
	if st, err := os.Stat(path); err != nil || st.IsDir() {
		return "Script not found: " + name
	}
	if h.RunScript == nil {
		return "skill_run failed: no script runner"
	}
	out, code, errMsg := h.RunScript(ctx, path, sysSplitArgs(fmtString(args["args"]), os.PathSeparator == '/'))
	if errMsg != "" {
		out = strings.TrimSpace(out + "\n" + errMsg)
	}
	if code != 0 || errMsg != "" {
		return fmt.Sprintf("skill_run failed (exit %d): %s", code, truncateRunes(out, 4000))
	}
	if out == "" {
		out = "(no output)"
	}
	return truncateRunes(out, 8000)
}

// ServeStdio is run_stdio_server: newline-delimited JSON-RPC on in/out until
// EOF. Nothing but responses is written to out.
func (h *MCPHost) ServeStdio(ctx context.Context, in io.Reader, out io.Writer) error {
	sc := bufio.NewScanner(in)
	sc.Buffer(make([]byte, 64*1024), 16<<20)
	enc := func(v any) error {
		raw, err := json.Marshal(v)
		if err != nil {
			return err
		}
		_, err = out.Write(append(raw, '\n'))
		return err
	}
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line == "" {
			continue
		}
		var msg any
		if err := json.Unmarshal([]byte(line), &msg); err != nil {
			if werr := enc(rpcErr(nil, -32700, "Parse error")); werr != nil {
				return werr
			}
			continue
		}
		if resp := h.Handle(ctx, msg); resp != nil {
			if err := enc(resp); err != nil {
				return err
			}
		}
	}
	return sc.Err()
}

// ErrReservedMCPPort refuses :7400, which belongs to remedy serve.
var ErrReservedMCPPort = errors.New("port 7400 is reserved for remedy serve (Go runtime); use 7401 or another port")

// InteropHTTPHandler is the `remedy mcp serve --http` app: the runtime's A2A
// and MCP routes with inbound turned on for this listener and bind as given.
func InteropHTTPHandler(home, version, host string, port int) http.Handler {
	s := newInteropOnlyServer(home, version)
	s.interopForceInbound = true
	s.interopBindHost = host
	s.apiListenPort = port
	s.mountInteropRoutes()
	s.ensureInteropToken()
	aliases := map[string]string{"/mcp/": "/mcp", "/a2a/v1/jsonrpc": "/a2a", "/a2a/extendedAgentCard": "/extendedAgentCard"}
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if to, ok := aliases[r.URL.Path]; ok {
			r2 := r.Clone(r.Context())
			r2.URL.Path = to
			r = r2
		}
		if _, pattern := s.mux.Handler(r); pattern == "" {
			writeJSON(w, http.StatusNotFound, map[string]any{"error": map[string]any{"message": "not found"}})
			return
		}
		s.mux.ServeHTTP(w, r)
	})
}

// ServeInteropHTTP listens on host:port until ctx ends (run_http).
func ServeInteropHTTP(ctx context.Context, home, version, host string, port int, onBound func(addr string)) error {
	if port == 7400 {
		return ErrReservedMCPPort
	}
	if strings.TrimSpace(host) == "" {
		host = "127.0.0.1"
	}
	ln, err := net.Listen("tcp", net.JoinHostPort(strings.Trim(host, "[]"), strconv.Itoa(port)))
	if err != nil {
		return err
	}
	srv := &http.Server{Handler: InteropHTTPHandler(home, version, host, port), ReadHeaderTimeout: 10 * time.Second}
	if onBound != nil {
		onBound(ln.Addr().String())
	}
	done := make(chan error, 1)
	go func() { done <- srv.Serve(ln) }()
	select {
	case <-ctx.Done():
		sctx, cancel := context.WithTimeout(context.Background(), 3*time.Second)
		defer cancel()
		_ = srv.Shutdown(sctx)
		return nil
	case err := <-done:
		if errors.Is(err, http.ErrServerClosed) {
			return nil
		}
		return err
	}
}

// ---- remedy uninstall ------------------------------------------------------------

// UninstallWipeVision is vision.install.wipe_vision_data: stop the vision
// server recorded in vision.json, then remove <home>/vision.
func UninstallWipeVision(home string) (map[string]any, error) {
	return newNativeVisionWorker().Uninstall(context.Background(), home, false)
}

// ---- remedy computer ---------------------------------------------------------------

// ComputerActions are the OS actions the CLI computer host performs
// (host_binding.native() in Python). Tests replace them; no test acts on the
// real machine.
type ComputerActions interface {
	OpenURL(url string) (map[string]any, error)
	Screenshot(home string) (map[string]any, error)
	DesktopSnapshot(limit int, mode string) ([]map[string]any, error)
	Click(x, y int, button string, clicks int) error
	TypeText(text string) error
	PressKey(key string) error
}

// CLIComputerHost is core/computer/cli_host.LocalComputerHost: a worker that
// marks the filesystem job queue as polled by "cli", claims pending jobs and
// completes each one, as failed when it cannot do it.
type CLIComputerHost struct {
	home    string
	bridge  *HostBridge
	Actions ComputerActions

	mu            sync.Mutex
	stop          chan struct{}
	done          chan struct{}
	jobsCompleted int
	lastAction    string
	lastError     string
	elements      []map[string]any
	idle          time.Duration
}

// NewCLIComputerHost makes a stopped host for home.
func NewCLIComputerHost(home string, actions ComputerActions) *CLIComputerHost {
	return &CLIComputerHost{home: ResolveHomeDir(home), bridge: newHostBridge(home), Actions: actions, idle: 80 * time.Millisecond}
}

// Running reports whether the worker is alive.
func (h *CLIComputerHost) Running() bool {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.runningLocked()
}

func (h *CLIComputerHost) runningLocked() bool {
	if h.done == nil {
		return false
	}
	select {
	case <-h.done:
		return false
	default:
		return true
	}
}

// HostConnected is bridge.host_connected().
func (h *CLIComputerHost) HostConnected() bool { return h.bridge.hostConnected() }

// PendingJobs is bridge.pending_count().
func (h *CLIComputerHost) PendingJobs() int { return h.bridge.pendingCount() }

// MarkDesktopPolling records a Desktop (rust) poll on this host's bridge, as
// jobs/next from the Tauri app does.
func (h *CLIComputerHost) MarkDesktopPolling() { h.bridge.markHostAlive(true, "rust") }

// LastError is the last failure message ("" when none).
func (h *CLIComputerHost) LastError() string {
	h.mu.Lock()
	defer h.mu.Unlock()
	return h.lastError
}

// StartCLI is start_cli_computer_host: it refuses while Desktop owns
// jobs/next (last error "desktop_owns_host"), else starts the worker.
func (h *CLIComputerHost) StartCLI() bool {
	if h.bridge.hostDriver() == "rust" {
		h.setErr("desktop_owns_host")
		return false
	}
	h.setErr("")
	return h.Start()
}

// Start runs the worker; a second Start while running adds no second worker.
func (h *CLIComputerHost) Start() bool {
	h.mu.Lock()
	if h.runningLocked() {
		h.mu.Unlock()
		return true
	}
	h.stop = make(chan struct{})
	h.done = make(chan struct{})
	stop, done := h.stop, h.done
	h.mu.Unlock()
	// The loop marks the host alive before its first wait, so the first
	// tick is enough for host_connected (Python waits up to 1.5 s for it).
	ready := make(chan struct{})
	go h.loop(stop, done, ready)
	select {
	case <-ready:
	case <-done:
	case <-time.After(1500 * time.Millisecond):
	}
	return h.Running()
}

// Stop ends the worker and reports whether it has gone. A worker that
// outlives timeout is kept (and false returned) unless force is set.
func (h *CLIComputerHost) Stop(timeout time.Duration, force bool) bool {
	h.mu.Lock()
	stop, done := h.stop, h.done
	if stop != nil {
		select {
		case <-stop:
		default:
			close(stop)
		}
	}
	h.mu.Unlock()
	if done != nil {
		select {
		case <-done:
		case <-time.After(timeout):
			if !force {
				return false
			}
		}
	}
	h.mu.Lock()
	h.done = nil
	h.stop = nil
	h.mu.Unlock()
	return true
}

// Wait blocks until the worker ends or ctx is done.
func (h *CLIComputerHost) Wait(ctx context.Context) {
	h.mu.Lock()
	done := h.done
	h.mu.Unlock()
	if done == nil {
		return
	}
	select {
	case <-done:
	case <-ctx.Done():
	}
}

// Status is LocalComputerHost.status().
func (h *CLIComputerHost) Status() map[string]any {
	connected := h.bridge.hostConnected()
	pending := h.bridge.pendingCount()
	h.mu.Lock()
	defer h.mu.Unlock()
	return map[string]any{
		"mode":           "cli",
		"running":        h.runningLocked(),
		"host_connected": connected,
		"pending_jobs":   pending,
		"jobs_completed": h.jobsCompleted,
		"last_action":    h.lastAction,
		"last_error":     h.lastError,
		"home":           h.home,
	}
}

func (h *CLIComputerHost) setErr(msg string) {
	h.mu.Lock()
	h.lastError = msg
	h.mu.Unlock()
}

func (h *CLIComputerHost) loop(stop, done, ready chan struct{}) {
	defer close(done)
	signalled := false
	for {
		select {
		case <-stop:
			return
		default:
		}
		if h.bridge.hostDriver() == "rust" {
			return // Desktop took the queue over
		}
		h.bridge.markHostAlive(true, "cli")
		if !signalled {
			close(ready)
			signalled = true
		}
		if cmd := h.bridge.takeUICommand(""); cmd != nil {
			h.HandleUI(cmd)
		}
		// Jobs are written by another process (the runtime), whose enqueue
		// never wakes this bridge's idle flag: rescan the folder every tick.
		h.bridge.mu.Lock()
		h.bridge.pollIdleEmpty = false
		h.bridge.mu.Unlock()
		if job := h.bridge.claimNext(nil, nil, "", 0); job != nil {
			h.HandleJob(job.ID, job.Action, job.Payload)
			continue
		}
		select {
		case <-stop:
			return
		case <-time.After(h.idle):
		}
	}
}

// HandleUI is _handle_ui: open_browser / navigate opens a vetted http(s) URL
// in the system browser; anything else is ignored.
func (h *CLIComputerHost) HandleUI(cmd map[string]any) {
	action := strings.ToLower(fmtString(cmd["action"]))
	if action == "" {
		action = strings.ToLower(fmtString(cmd["job_action"]))
	}
	raw := strings.TrimSpace(fmtString(cmd["url"]))
	if (action != "open_browser" && action != "navigate") || raw == "" {
		return
	}
	cleaned := sysVetBrowserURL(raw)
	if cleaned == "" {
		h.setErr(fmt.Sprintf("ui open refused invalid URL: %q", truncateRunes(raw, 80)))
		return
	}
	if _, err := h.Actions.OpenURL(cleaned); err != nil {
		h.setErr("ui open failed: " + err.Error())
		return
	}
	h.mu.Lock()
	h.lastAction = "ui_open:" + truncateRunes(cleaned, 80)
	h.mu.Unlock()
}

// sysVetBrowserURL is normalize_url + is_valid_navigate_url, then open_url's
// own refusal of anything but http(s) or with control bytes.
func sysVetBrowserURL(raw string) string {
	if strings.ContainsAny(raw, "\r\n\x00") {
		return ""
	}
	u := normalizeNavigateURL(raw)
	if u == "" || !isValidNavigateURL(u) {
		return ""
	}
	low := strings.ToLower(u)
	if !strings.HasPrefix(low, "http://") && !strings.HasPrefix(low, "https://") {
		return ""
	}
	return u
}

// HandleJob is _handle_job: run the action and always complete the job, as
// failed when the action refused or broke.
func (h *CLIComputerHost) HandleJob(id, action string, payload map[string]any) {
	act := strings.ToLower(strings.TrimSpace(action))
	h.mu.Lock()
	h.lastAction = act
	h.mu.Unlock()
	if payload == nil {
		payload = map[string]any{}
	}
	result, err := h.runActionSafe(act, payload)
	if err != nil {
		msg := err.Error()
		h.setErr(msg)
		h.bridge.complete(id, false, map[string]any{"ok": false, "target": "cli", "action": act, "message": "CLI host error: " + msg}, &msg)
		return
	}
	ok := true
	if v, has := result["ok"]; has {
		ok, _ = v.(bool)
	}
	if ok {
		h.bridge.complete(id, true, result, nil)
		h.mu.Lock()
		h.jobsCompleted++
		h.lastError = ""
		h.mu.Unlock()
		return
	}
	msg := fmtString(result["message"])
	if msg == "" {
		msg = "failed"
	}
	h.bridge.complete(id, false, result, &msg)
	h.setErr(msg)
}

func (h *CLIComputerHost) runActionSafe(act string, payload map[string]any) (res map[string]any, err error) {
	defer func() {
		if r := recover(); r != nil {
			err = fmt.Errorf("%v", r)
		}
	}()
	return h.RunAction(act, payload)
}

func sysPublicResult(ok bool, target, action, message string, extra map[string]any) map[string]any {
	out := map[string]any{"ok": ok, "target": target, "action": action, "message": message}
	for k, v := range extra {
		out[k] = v
	}
	return out
}

func sysNorm(s string) string { return strings.Join(strings.Fields(strings.ToLower(s)), " ") }

// sysBestElement is a cut-down elements.find_best_element: exact name or
// placeholder, then a contained query, then a contained name (min score 20).
func sysBestElement(els []map[string]any, query string) map[string]any {
	q := sysNorm(query)
	if q == "" {
		return nil
	}
	var best map[string]any
	bestScore := 0.0
	for _, el := range els {
		name := sysNorm(fmtString(el["name"]))
		ph := sysNorm(fmtString(el["placeholder"]))
		label := sysNorm(strings.Join([]string{fmtString(el["name"]), fmtString(el["label"]), fmtString(el["text"]), fmtString(el["placeholder"])}, " "))
		score := 0.0
		switch {
		case name == q || (ph != "" && ph == q):
			score += 100
		case strings.Contains(name, q) || strings.Contains(ph, q):
			score += 70
		case len(name) >= 3 && strings.Contains(q, name):
			score += 40
		}
		if strings.Contains(label, q) {
			score += 25
		}
		if score >= 20 && score > bestScore {
			best, bestScore = el, score
		}
	}
	return best
}

func sysNum(v any) (int, bool) {
	switch t := v.(type) {
	case float64:
		return int(t), true
	case int:
		return t, true
	case int64:
		return int(t), true
	case json.Number:
		f, err := t.Float64()
		return int(f), err == nil
	case string:
		f, err := strconv.ParseFloat(strings.TrimSpace(t), 64)
		return int(f), err == nil
	}
	return 0, false
}

func sysElementXY(el map[string]any) (int, int) {
	x, ok := sysNum(el["x"])
	if !ok {
		x, _ = sysNum(el["cx"])
	}
	y, ok := sysNum(el["y"])
	if !ok {
		y, _ = sysNum(el["cy"])
	}
	return x, y
}

func (h *CLIComputerHost) snapshot(limit int, mode string) ([]map[string]any, error) {
	els, err := h.Actions.DesktopSnapshot(limit, mode)
	if err != nil {
		return nil, err
	}
	h.mu.Lock()
	h.elements = els
	h.mu.Unlock()
	h.bridge.mu.Lock()
	h.bridge.rememberElements("", els)
	h.bridge.mu.Unlock()
	return els, nil
}

func (h *CLIComputerHost) elementByRef(ref string) map[string]any {
	h.mu.Lock()
	defer h.mu.Unlock()
	for _, el := range h.elements {
		if fmtString(el["ref"]) == ref {
			return el
		}
	}
	return nil
}

// RunAction is _run_action.
func (h *CLIComputerHost) RunAction(act string, payload map[string]any) (map[string]any, error) {
	str := func(k string) string { return strings.TrimSpace(fmtString(payload[k])) }
	button := str("button")
	if button == "" {
		button = "left"
	}
	clicks, ok := sysNum(payload["clicks"])
	if !ok || clicks <= 0 {
		clicks = 1
	}
	switch act {
	case "navigate", "open":
		raw := fmtString(payload["url"])
		u := sysVetBrowserURL(raw)
		if u == "" {
			return sysPublicResult(false, "cli", "navigate", fmt.Sprintf("Invalid URL for CLI host: %q", truncateRunes(raw, 100)), nil), nil
		}
		info, err := h.Actions.OpenURL(u)
		if err != nil {
			return nil, err
		}
		h.bridge.markObservedURL(u, "")
		extra := map[string]any{}
		for k, v := range info {
			extra[k] = v
		}
		extra["via"], extra["url"], extra["ready_for_input"] = "cli_host", u, true
		return sysPublicResult(true, "cli", "navigate", fmt.Sprintf("CLI host opened system browser: %s. (In-app Browser rail requires Remedy Desktop.)", u), extra), nil
	case "ready":
		return sysPublicResult(true, "cli", "ready", "CLI host ready", map[string]any{"via": "cli_host"}), nil
	case "screenshot":
		info, err := h.Actions.Screenshot(h.home)
		if err != nil {
			return nil, err
		}
		extra := map[string]any{}
		for k, v := range info {
			extra[k] = v
		}
		extra["via"] = "cli_host"
		return sysPublicResult(true, "desktop", "screenshot", fmt.Sprintf("CLI host desktop screenshot (%v x %v)", info["width"], info["height"]), extra), nil
	case "snapshot", "a11y":
		limit, ok := sysNum(payload["limit"])
		if !ok || limit <= 0 {
			limit = 40
		}
		mode := str("mode")
		if mode == "" {
			mode = "auto"
		}
		els, err := h.snapshot(limit, mode)
		if err != nil {
			return nil, err
		}
		return sysPublicResult(true, "desktop", "snapshot", fmt.Sprintf("CLI host desktop snapshot: %d elements (no Browser rail DOM without Desktop)", len(els)),
			map[string]any{"elements": els, "via": "cli_host"}), nil
	case "page_text":
		return sysPublicResult(false, "cli", "page_text", "page_text needs Browser rail DOM — start Remedy Desktop, or use computer_snapshot / desktop tools from CLI.", map[string]any{"via": "cli_host"}), nil
	case "click":
		if ref := str("ref"); ref != "" {
			el := h.elementByRef(ref)
			if el == nil {
				if _, err := h.snapshot(60, "auto"); err != nil {
					return nil, err
				}
				el = h.elementByRef(ref)
			}
			if el != nil {
				x, y := sysElementXY(el)
				if err := h.Actions.Click(x, y, button, clicks); err != nil {
					return nil, err
				}
				return sysPublicResult(true, "desktop", "click", "CLI host clicked ref="+ref, map[string]any{"ref": ref, "via": "cli_host"}), nil
			}
		}
		if q := str("text"); q != "" {
			els, err := h.snapshot(60, "auto")
			if err != nil {
				return nil, err
			}
			if el := sysBestElement(els, q); el != nil {
				x, y := sysElementXY(el)
				if err := h.Actions.Click(x, y, "left", 1); err != nil {
					return nil, err
				}
				return sysPublicResult(true, "desktop", "click", fmt.Sprintf("CLI host clicked text=%q", q), map[string]any{"text": q, "ref": el["ref"], "via": "cli_host"}), nil
			}
		}
		x, okx := sysNum(payload["x"])
		y, oky := sysNum(payload["y"])
		if okx && oky {
			if err := h.Actions.Click(x, y, button, clicks); err != nil {
				return nil, err
			}
			return sysPublicResult(true, "desktop", "click", fmt.Sprintf("CLI host clicked (%v,%v)", payload["x"], payload["y"]), map[string]any{"x": payload["x"], "y": payload["y"], "via": "cli_host"}), nil
		}
	case "type":
		text := fmtString(payload["text"])
		if text == "" {
			text = fmtString(payload["content"])
		}
		if text != "" {
			if err := h.Actions.TypeText(text); err != nil {
				return nil, err
			}
			return sysPublicResult(true, "desktop", "type", fmt.Sprintf("CLI host typed %d chars", len([]rune(text))), map[string]any{"via": "cli_host"}), nil
		}
	case "key":
		key := fmtString(payload["key"])
		if key == "" {
			key = fmtString(payload["keys"])
		}
		if key != "" {
			if err := h.Actions.PressKey(key); err != nil {
				return nil, err
			}
			return sysPublicResult(true, "desktop", "key", "CLI host pressed "+key, map[string]any{"via": "cli_host"}), nil
		}
	}
	switch act {
	case "click", "type", "key", "scroll", "drag":
		return sysPublicResult(false, "cli", act, fmt.Sprintf("CLI host could not complete %s — need coords/ref/text or start Remedy Desktop for Browser rail controls.", act), map[string]any{"via": "cli_host"}), nil
	}
	keys := make([]string, 0, len(payload))
	for k := range payload {
		keys = append(keys, k)
	}
	sort.Strings(keys)
	if len(keys) > 12 {
		keys = keys[:12]
	}
	return sysPublicResult(false, "cli", act, fmt.Sprintf("CLI host cannot complete %s. Start Remedy Desktop for Browser rail controls (fill/select/press_hold/act/hover).", act),
		map[string]any{"via": "cli_host", "payload_keys": keys}), nil
}

// EnqueueComputerJob writes a pending job into home's queue, as the runtime
// does for computer_* tools; it returns the job id.
func EnqueueComputerJob(home, action string, payload map[string]any) string {
	return newHostBridge(home).Enqueue(action, payload, "").ID
}

// ComputerJobState reads a job back: its status, result and error.
func ComputerJobState(home, id string) (status string, result map[string]any, errMsg string) {
	job := newHostBridge(home).readJob(id)
	if job == nil {
		return "", nil, ""
	}
	if job.Error != nil {
		errMsg = *job.Error
	}
	return job.Status, job.Result, errMsg
}
