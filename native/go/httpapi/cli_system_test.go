package httpapi

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

// ---- setup -------------------------------------------------------------------

func TestCLISystemSetupWriteConfigMovesEveryKeyToTheSecretStore(t *testing.T) {
	home := t.TempDir()
	key := "xai-" + strings.Repeat("k", 40)
	cfg := map[string]any{
		"name": "Remedy", "llm_provider": "xai", "llm_api_key": key,
		"provider_keys":    map[string]any{"openai": "sk-test-openai-123"},
		"telegram":         map[string]any{"bot_token": "tg-secret-1"},
		"enabled_channels": []string{"telegram"},
		"gateway":          map[string]any{"heartbeat_interval": 60},
		"home_dir":         filepath.ToSlash(home),
		"project_path":     `C:\work\proj`,
	}
	path, err := SetupWriteConfig(home, cfg)
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	text := string(raw)
	for _, s := range []string{key, "sk-test-openai-123", "tg-secret-1"} {
		if strings.Contains(text, s) {
			t.Fatalf("config.toml carries a plaintext secret %q:\n%s", s, text)
		}
	}
	if got := secret.GetProviderSecret(home, "xai"); got != key {
		t.Fatalf("xai key not in the secret store: %q", got)
	}
	if got := secret.GetProviderSecret(home, "openai"); got != "sk-test-openai-123" {
		t.Fatalf("provider_keys entry not stored: %q", got)
	}
	if got := secret.GetProviderSecret(home, "ch:telegram:bot_token"); got != "tg-secret-1" {
		t.Fatalf("telegram token not stored: %q", got)
	}
	if !strings.Contains(text, "setup_completed = true") || !strings.Contains(text, `project_path = "C:/work/proj"`) {
		t.Fatalf("missing setup flag or path normalisation:\n%s", text)
	}
	// Scalars before tables: no root key after the first [table].
	firstTable := strings.Index(text, "\n[")
	if firstTable < 0 {
		t.Fatal("no table written")
	}
	for _, line := range strings.Split(text[firstTable:], "\n") {
		if strings.HasPrefix(line, "name =") || strings.HasPrefix(line, "setup_completed =") {
			t.Fatalf("root key %q written after a table:\n%s", line, text)
		}
	}
}

func TestCLISystemSetupWriteConfigKeepsAnExplicitSetupFlag(t *testing.T) {
	home := t.TempDir()
	path, err := SetupWriteConfig(home, map[string]any{"setup_completed": false})
	if err != nil {
		t.Fatal(err)
	}
	raw, _ := os.ReadFile(path)
	if !strings.Contains(string(raw), "setup_completed = false") {
		t.Fatalf("explicit flag overwritten:\n%s", raw)
	}
}

// ---- mcp -----------------------------------------------------------------------

func sysTestSkillRoot(t *testing.T) string {
	t.Helper()
	root := t.TempDir()
	dir := filepath.Join(root, "demo-skill")
	if err := os.MkdirAll(filepath.Join(dir, "scripts"), 0o755); err != nil {
		t.Fatal(err)
	}
	md := "---\nname: demo-skill\ndescription: says hello\n---\n# Demo\nSay hello.\n"
	if err := os.WriteFile(filepath.Join(dir, "SKILL.md"), []byte(md), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(dir, "scripts", "hello.py"), []byte("print('hi')\n"), 0o600); err != nil {
		t.Fatal(err)
	}
	return root
}

func sysStdio(t *testing.T, h *MCPHost, lines ...string) []map[string]any {
	t.Helper()
	var out bytes.Buffer
	if err := h.ServeStdio(context.Background(), strings.NewReader(strings.Join(lines, "\n")+"\n"), &out); err != nil {
		t.Fatal(err)
	}
	var res []map[string]any
	for _, l := range strings.Split(strings.TrimSpace(out.String()), "\n") {
		if l == "" {
			continue
		}
		var m map[string]any
		if err := json.Unmarshal([]byte(l), &m); err != nil {
			t.Fatalf("stdout carries a non-JSON line %q", l)
		}
		res = append(res, m)
	}
	return res
}

func sysResultText(t *testing.T, m map[string]any) string {
	t.Helper()
	res, _ := m["result"].(map[string]any)
	content, _ := res["content"].([]any)
	if len(content) == 0 {
		t.Fatalf("no content in %v", m)
	}
	first, _ := content[0].(map[string]any)
	s, _ := first["text"].(string)
	return s
}

func TestCLISystemMCPStdioSpeaksJSONRPC(t *testing.T) {
	h := NewMCPHost(t.TempDir(), "9.9.9")
	h.SetSkillRoots(sysTestSkillRoot(t))
	got := sysStdio(t, h,
		`{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26"}}`,
		`{"jsonrpc":"2.0","method":"notifications/initialized"}`,
		`{ not json`,
		`{"jsonrpc":"2.0","id":2,"method":"initialize","params":{"protocolVersion":"1999-01-01"}}`,
		`{"jsonrpc":"2.0","id":3,"method":"tools/list"}`,
		`{"jsonrpc":"2.0","id":4,"method":"tools/call","params":{"name":"remedy_skill_list","arguments":{}}}`,
		`{"jsonrpc":"2.0","id":5,"method":"tools/call","params":{"name":"remedy_skill_get","arguments":{"name":"demo-skill"}}}`,
		`{"jsonrpc":"2.0","id":6,"method":"tools/call","params":{"name":"nope","arguments":{}}}`,
		`{"jsonrpc":"2.0","id":7,"method":"ping"}`,
	)
	if len(got) != 8 {
		t.Fatalf("want 8 replies (notification answered with nothing), got %d: %v", len(got), got)
	}
	init := got[0]["result"].(map[string]any)
	if init["protocolVersion"] != "2025-03-26" {
		t.Fatalf("requested version not echoed: %v", init)
	}
	if info := init["serverInfo"].(map[string]any); info["name"] != "remedy" || info["version"] != "9.9.9" {
		t.Fatalf("serverInfo: %v", info)
	}
	if e := got[1]["error"].(map[string]any); e["code"].(float64) != -32700 {
		t.Fatalf("parse error: %v", got[1])
	}
	if v := got[2]["result"].(map[string]any)["protocolVersion"]; v != sysMCPLatest {
		t.Fatalf("unknown version negotiates to latest, got %v", v)
	}
	names := map[string]bool{}
	for _, tl := range got[3]["result"].(map[string]any)["tools"].([]any) {
		names[tl.(map[string]any)["name"].(string)] = true
	}
	for _, want := range []string{"remedy_skill_list", "remedy_skill_search", "remedy_skill_get", "remedy_skill_run", "remedy_plan_list", "remedy_plan_show"} {
		if !names[want] {
			t.Fatalf("tools/list missing %s: %v", want, names)
		}
	}
	// stdio offers exactly the runtime's /mcp talk tools too.
	for _, tool := range interopMCPTools() {
		if !names[fmtString(tool["name"])] {
			t.Fatalf("stdio misses runtime talk tool %v", tool["name"])
		}
	}
	if s := sysResultText(t, got[4]); !strings.Contains(s, "demo-skill") {
		t.Fatalf("skill_list: %q", s)
	}
	if s := sysResultText(t, got[5]); !strings.HasPrefix(s, "# demo-skill") || !strings.Contains(s, "Say hello.") {
		t.Fatalf("skill_get: %q", s)
	}
	if s := sysResultText(t, got[6]); s != "Unknown tool: nope" {
		t.Fatalf("unknown tool: %q", s)
	}
	if got[7]["error"] != nil {
		t.Fatalf("ping: %v", got[7])
	}
}

func TestCLISystemMCPSkillRunNeedsOptInAndStaysInScripts(t *testing.T) {
	root := sysTestSkillRoot(t)
	h := NewMCPHost(t.TempDir(), "1")
	h.SetSkillRoots(root)
	var ran []string
	h.RunScript = func(_ context.Context, script string, args []string) (string, int, string) {
		ran = append(ran, script+"|"+strings.Join(args, ","))
		return "hi\n", 0, ""
	}
	call := func(args map[string]any) string {
		s, _ := h.callTool(context.Background(), "remedy_skill_run", args)
		return s
	}
	t.Setenv("REMEDY_MCP_ALLOW_RUN", "")
	if s := call(map[string]any{"name": "demo-skill"}); !strings.Contains(s, "disabled over MCP") || len(ran) != 0 {
		t.Fatalf("ran without opt-in: %q %v", s, ran)
	}
	t.Setenv("REMEDY_MCP_ALLOW_RUN", "1")
	for _, bad := range []string{"../SKILL.md", "/etc/passwd", `..\..\x.py`, "C:/x.py"} {
		if s := call(map[string]any{"name": "demo-skill", "script": bad}); s != "Script path escapes skill scripts/ directory" {
			t.Fatalf("%q not jailed: %q", bad, s)
		}
	}
	outside := filepath.Join(t.TempDir(), "evil.py")
	if err := os.WriteFile(outside, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.Symlink(outside, filepath.Join(root, "demo-skill", "scripts", "link.py")); err == nil {
		if s := call(map[string]any{"name": "demo-skill", "script": "link.py"}); s != "Script path escapes skill scripts/ directory" {
			t.Fatalf("symlink escape not jailed: %q", s)
		}
	}
	if len(ran) != 0 {
		t.Fatalf("a refused script ran: %v", ran)
	}
	if s := call(map[string]any{"name": "demo-skill", "script": "hello.py", "args": `a "b c"`}); s != "hi\n" {
		t.Fatalf("run output: %q", s)
	}
	if len(ran) != 1 || !strings.HasSuffix(ran[0], filepath.Join("scripts", "hello.py")+"|a,b c") {
		t.Fatalf("ran: %v", ran)
	}
}

func TestCLISystemMCPHTTPRefusesTheRuntimePort(t *testing.T) {
	err := ServeInteropHTTP(context.Background(), t.TempDir(), "1", "127.0.0.1", 7400, nil)
	if !errors.Is(err, ErrReservedMCPPort) {
		t.Fatalf("7400 accepted: %v", err)
	}
}

func TestCLISystemMCPHTTPServesTheRuntimeDispatch(t *testing.T) {
	home := t.TempDir()
	srv := httptest.NewServer(InteropHTTPHandler(home, "2.0.0", "127.0.0.1", 7401))
	defer srv.Close()
	tok, err := os.ReadFile(filepath.Join(home, "interop", "inbound_token"))
	if err != nil || strings.TrimSpace(string(tok)) == "" {
		t.Fatalf("inbound token not ensured: %v", err)
	}
	post := func(path, auth, body string) (*http.Response, map[string]any) {
		req, _ := http.NewRequest(http.MethodPost, srv.URL+path, strings.NewReader(body))
		req.Header.Set("Content-Type", "application/json")
		req.Header.Set("Accept", "application/json")
		if auth != "" {
			req.Header.Set("Authorization", "Bearer "+auth)
		}
		resp, err := http.DefaultClient.Do(req)
		if err != nil {
			t.Fatal(err)
		}
		defer resp.Body.Close()
		var m map[string]any
		_ = json.NewDecoder(resp.Body).Decode(&m)
		return resp, m
	}
	if resp, _ := post("/mcp", "", `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`); resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("no bearer: %d", resp.StatusCode)
	}
	resp, m := post("/mcp", strings.TrimSpace(string(tok)), `{"jsonrpc":"2.0","id":1,"method":"tools/list"}`)
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("tools/list: %d %v", resp.StatusCode, m)
	}
	tools, _ := m["result"].(map[string]any)["tools"].([]any)
	if len(tools) != len(interopMCPTools()) {
		t.Fatalf("HTTP tools differ from the runtime's /mcp: %v", tools)
	}
	if resp, _ := post("/mcp/", strings.TrimSpace(string(tok)), `{"jsonrpc":"2.0","id":2,"method":"ping"}`); resp.StatusCode != http.StatusOK {
		t.Fatalf("/mcp/ alias: %d", resp.StatusCode)
	}
	if resp, m := post("/nope", "", `{}`); resp.StatusCode != http.StatusNotFound || m["error"] == nil {
		t.Fatalf("unknown path: %d %v", resp.StatusCode, m)
	}
	cr, err := http.Get(srv.URL + "/.well-known/agent-card.json")
	if err != nil || cr.StatusCode != http.StatusOK {
		t.Fatalf("agent card: %v %v", err, cr)
	}
	cr.Body.Close()
}

// ---- computer host -------------------------------------------------------------

type sysFakeActions struct {
	mu     sync.Mutex
	opened []string
	clicks [][2]int
	typed  []string
	keys   []string
	panicT bool
	els    []map[string]any
}

func (f *sysFakeActions) OpenURL(u string) (map[string]any, error) {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.opened = append(f.opened, u)
	return map[string]any{"opened": true}, nil
}
func (f *sysFakeActions) Screenshot(string) (map[string]any, error) {
	return map[string]any{"width": 10, "height": 20, "path": "x.png"}, nil
}
func (f *sysFakeActions) DesktopSnapshot(int, string) ([]map[string]any, error) { return f.els, nil }
func (f *sysFakeActions) Click(x, y int, _ string, _ int) error {
	f.mu.Lock()
	defer f.mu.Unlock()
	f.clicks = append(f.clicks, [2]int{x, y})
	return nil
}
func (f *sysFakeActions) TypeText(s string) error {
	if f.panicT {
		panic("boom")
	}
	f.typed = append(f.typed, s)
	return nil
}
func (f *sysFakeActions) PressKey(k string) error { f.keys = append(f.keys, k); return nil }

func sysRunJob(t *testing.T, home string, h *CLIComputerHost, action string, payload map[string]any) (string, map[string]any, string) {
	t.Helper()
	id := EnqueueComputerJob(home, action, payload)
	h.HandleJob(id, action, payload)
	return ComputerJobState(home, id)
}

func TestCLISystemComputerHostCompletesEveryJobEvenWhenItFails(t *testing.T) {
	home := t.TempDir()
	fa := &sysFakeActions{els: []map[string]any{{"ref": "d1", "name": "Save", "x": 5.0, "y": 6.0}}}
	h := NewCLIComputerHost(home, fa)

	if st, res, _ := sysRunJob(t, home, h, "navigate", map[string]any{"url": "example.com/a"}); st != "done" || res["url"] != "https://example.com/a" {
		t.Fatalf("navigate: %s %v", st, res)
	}
	for _, bad := range []string{"javascript:alert(1)", "file:///etc/passwd", "https://exa\nmple.com", ""} {
		st, _, msg := sysRunJob(t, home, h, "navigate", map[string]any{"url": bad})
		if st != "error" || !strings.Contains(msg, "Invalid URL") {
			t.Fatalf("%q: %s %q", bad, st, msg)
		}
	}
	if len(fa.opened) != 1 {
		t.Fatalf("a refused URL reached the browser: %v", fa.opened)
	}
	if st, _, _ := sysRunJob(t, home, h, "click", map[string]any{"text": "save"}); st != "done" || len(fa.clicks) != 1 || fa.clicks[0] != [2]int{5, 6} {
		t.Fatalf("click by text: %s %v", st, fa.clicks)
	}
	if st, _, _ := sysRunJob(t, home, h, "click", map[string]any{"x": 1.0, "y": 2.0}); st != "done" || fa.clicks[1] != [2]int{1, 2} {
		t.Fatalf("click xy: %s %v", st, fa.clicks)
	}
	if st, _, msg := sysRunJob(t, home, h, "click", map[string]any{}); st != "error" || !strings.Contains(msg, "could not complete click") {
		t.Fatalf("click without target: %s %q", st, msg)
	}
	if st, _, msg := sysRunJob(t, home, h, "fill", map[string]any{"ref": "x"}); st != "error" || !strings.Contains(msg, "cannot complete fill") {
		t.Fatalf("unsupported action: %s %q", st, msg)
	}
	fa.panicT = true
	if st, _, msg := sysRunJob(t, home, h, "type", map[string]any{"text": "hi"}); st != "error" || !strings.Contains(msg, "boom") {
		t.Fatalf("a panicking action must still complete the job: %s %q", st, msg)
	}
	if st, _, _ := sysRunJob(t, home, h, "key", map[string]any{"key": "ctrl+s"}); st != "done" || fa.keys[0] != "ctrl+s" {
		t.Fatalf("key: %s %v", st, fa.keys)
	}
	st := h.Status()
	if st["mode"] != "cli" || st["jobs_completed"].(int) != 4 || st["last_action"] != "key" {
		t.Fatalf("status: %v", st)
	}
	if h.PendingJobs() != 0 {
		t.Fatalf("jobs left pending: %d", h.PendingJobs())
	}
}

func TestCLISystemComputerHostClaimsJobsWrittenByAnotherProcess(t *testing.T) {
	home := t.TempDir()
	fa := &sysFakeActions{}
	h := NewCLIComputerHost(home, fa)
	h.idle = 5 * time.Millisecond
	if !h.StartCLI() || !h.Running() || !h.HostConnected() {
		t.Fatalf("host did not start: %v", h.Status())
	}
	defer h.Stop(time.Second, true)
	// Let the loop see an empty queue first (the idle optimisation), then
	// enqueue from a separate bridge, as the runtime process would.
	time.Sleep(30 * time.Millisecond)
	id := EnqueueComputerJob(home, "ready", map[string]any{})
	deadline := time.Now().Add(5 * time.Second)
	for {
		st, _, _ := ComputerJobState(home, id)
		if st == "done" {
			break
		}
		if time.Now().After(deadline) {
			t.Fatalf("job never claimed: %s", st)
		}
		time.Sleep(10 * time.Millisecond)
	}
	if !h.Stop(time.Second, false) || h.Running() {
		t.Fatal("host did not stop")
	}
}

func TestCLISystemComputerHostYieldsToDesktop(t *testing.T) {
	h := NewCLIComputerHost(t.TempDir(), &sysFakeActions{})
	h.MarkDesktopPolling()
	if h.StartCLI() || h.Running() {
		t.Fatal("CLI host started while Desktop owns jobs/next")
	}
	if h.LastError() != "desktop_owns_host" {
		t.Fatalf("last error: %q", h.LastError())
	}
}

func TestCLISystemComputerHostUIOpenIsVetted(t *testing.T) {
	fa := &sysFakeActions{}
	h := NewCLIComputerHost(t.TempDir(), fa)
	h.HandleUI(map[string]any{"action": "open_browser", "url": "javascript:alert(1)"})
	h.HandleUI(map[string]any{"action": "delete_everything", "url": "https://example.com"})
	h.HandleUI(map[string]any{"action": "navigate", "url": "https://example.com/x"})
	if len(fa.opened) != 1 || fa.opened[0] != "https://example.com/x" {
		t.Fatalf("opened: %v", fa.opened)
	}
}
