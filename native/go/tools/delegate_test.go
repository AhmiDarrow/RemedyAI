package tools

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"testing"
	"time"
)

// The delegate tests need a coding agent that behaves like `claude -p
// --output-format stream-json`: emits events while it works, then a final
// result. The test binary itself plays that part — copied onto PATH under the
// agent's name, it is a real executable the authorized spawn can start (a .cmd
// script is not), and it needs no toolchain at test time.

const (
	fakeAgentModeEnv = "REMEDY_FAKE_AGENT_MODE"
	fakeAgentDirEnv  = "REMEDY_FAKE_AGENT_DIR"
)

func TestMain(m *testing.M) {
	switch os.Getenv(fakeAgentModeEnv) {
	case "":
		os.Exit(m.Run())
	case "stream":
		os.Exit(fakeAgentStream(false))
	case "fail":
		os.Exit(fakeAgentStream(true))
	case "hang":
		os.Exit(fakeAgentHang())
	case "heartbeat":
		os.Exit(fakeAgentHeartbeat())
	default:
		fmt.Fprintln(os.Stderr, "fake agent: unknown mode")
		os.Exit(9)
	}
}

// fakeAgentStream writes a realistic stream-json sequence with pauses between
// events, so a test that sees them in order has seen real streaming.
func fakeAgentStream(failing bool) int {
	recordFakeAgentInvocation()
	lines := []string{
		`{"type":"system","subtype":"init","session_id":"sess_fake","model":"claude-opus-5","tools":["Read","Edit"]}`,
		`{"type":"assistant","message":{"id":"msg_1","role":"assistant","content":[{"type":"text","text":"Reading main.go\nbefore I change it."}]},"session_id":"sess_fake"}`,
		`{"type":"assistant","message":{"id":"msg_2","role":"assistant","content":[{"type":"tool_use","id":"toolu_1","name":"Edit","input":{"file_path":"main.go","old_string":"a","new_string":"b"}}]},"session_id":"sess_fake"}`,
		`{"type":"user","message":{"role":"user","content":[{"type":"tool_result","tool_use_id":"toolu_1","content":[{"type":"text","text":"The file main.go has been updated."}]}]},"session_id":"sess_fake"}`,
	}
	if failing {
		lines = append(lines,
			`{"type":"result","subtype":"error_during_execution","is_error":true,"duration_ms":800,"result":"The build failed: undefined: Foo.","session_id":"sess_fake"}`)
	} else {
		lines = append(lines,
			`{"type":"result","subtype":"success","is_error":false,"duration_ms":1234,"num_turns":3,"result":"Changed main.go and ran the build.","session_id":"sess_fake","total_cost_usd":0.02}`)
	}
	for _, line := range lines {
		fmt.Println(line)
		time.Sleep(20 * time.Millisecond)
	}
	if failing {
		fmt.Fprintln(os.Stderr, "fake agent: the build failed")
		return 2
	}
	return 0
}

// fakeAgentHang starts a grandchild and then never finishes: both write a
// heartbeat file, so a test can prove the whole tree stopped.
func fakeAgentHang() int {
	recordFakeAgentInvocation()
	dir := os.Getenv(fakeAgentDirEnv)
	self, err := os.Executable()
	if err == nil {
		child := exec.Command(self)
		child.Env = append(os.Environ(), fakeAgentModeEnv+"=heartbeat")
		if startErr := child.Start(); startErr == nil {
			defer func() { _ = child.Process.Kill() }()
		}
	}
	fmt.Println(`{"type":"system","subtype":"init","session_id":"sess_hang","model":"claude-opus-5"}`)
	return beatUntilKilled(filepath.Join(dir, "child.beat"))
}

func fakeAgentHeartbeat() int {
	return beatUntilKilled(filepath.Join(os.Getenv(fakeAgentDirEnv), "grandchild.beat"))
}

func beatUntilKilled(path string) int {
	if path == "" || filepath.Base(path) == path {
		return 8
	}
	for i := 0; i < 6000; i++ {
		_ = os.WriteFile(path, []byte(fmt.Sprintf("%d", i)), 0o600)
		time.Sleep(50 * time.Millisecond)
	}
	return 0
}

// recordFakeAgentInvocation writes down how the agent was actually invoked:
// the argv Remedy built, the folder it was started in, and whether it inherited
// the no-further-delegation marker.
func recordFakeAgentInvocation() {
	dir := os.Getenv(fakeAgentDirEnv)
	if dir == "" {
		return
	}
	wd, _ := os.Getwd()
	body, err := json.Marshal(map[string]any{
		"argv":  os.Args[1:],
		"cwd":   wd,
		"depth": os.Getenv(DelegateDepthEnv),
	})
	if err != nil {
		return
	}
	_ = os.WriteFile(filepath.Join(dir, "agent.json"), body, 0o600)
}

// ---------------------------------------------------------------------------
// Harness
// ---------------------------------------------------------------------------

type fakeAgent struct {
	dir     string
	project string
}

// installFakeAgent copies the test binary onto PATH as `claude` and points the
// agent's scratch files at a temp directory.
func installFakeAgent(t *testing.T, mode string) fakeAgent {
	t.Helper()
	self, err := os.Executable()
	if err != nil {
		t.Skipf("test binary path unavailable: %v", err)
	}
	binDir := t.TempDir()
	name := "claude"
	if runtime.GOOS == "windows" {
		name += ".exe"
	}
	data, err := os.ReadFile(self)
	if err != nil {
		t.Skipf("test binary could not be copied: %v", err)
	}
	target := filepath.Join(binDir, name)
	if err := os.WriteFile(target, data, 0o755); err != nil {
		t.Fatalf("install fake agent: %v", err)
	}
	scratch := t.TempDir()
	project := t.TempDir()
	t.Setenv("PATH", binDir+string(os.PathListSeparator)+os.Getenv("PATH"))
	t.Setenv(fakeAgentModeEnv, mode)
	t.Setenv(fakeAgentDirEnv, scratch)
	return fakeAgent{dir: scratch, project: project}
}

// invocation is what the fake agent recorded about its own start.
type invocation struct {
	Argv  []string `json:"argv"`
	Cwd   string   `json:"cwd"`
	Depth string   `json:"depth"`
}

func (f fakeAgent) invocation(t *testing.T) invocation {
	t.Helper()
	data, err := os.ReadFile(filepath.Join(f.dir, "agent.json"))
	if err != nil {
		t.Fatalf("the agent recorded no invocation: %v", err)
	}
	var inv invocation
	if err := json.Unmarshal(data, &inv); err != nil {
		t.Fatal(err)
	}
	return inv
}

// recorder collects the progress events a run mirrors into the trail.
type recorder struct {
	mu     sync.Mutex
	events []ProgressEvent
}

func (r *recorder) sink() ProgressFunc {
	return func(ev ProgressEvent) {
		r.mu.Lock()
		defer r.mu.Unlock()
		r.events = append(r.events, ev)
	}
}

func (r *recorder) all() []ProgressEvent {
	r.mu.Lock()
	defer r.mu.Unlock()
	return append([]ProgressEvent(nil), r.events...)
}

// indexOf returns the position of the first event matching pred, or -1.
func indexOf(events []ProgressEvent, pred func(ProgressEvent) bool) int {
	for i, ev := range events {
		if pred(ev) {
			return i
		}
	}
	return -1
}

func delegateInput(t *testing.T, project string, extra map[string]any) json.RawMessage {
	t.Helper()
	body := map[string]any{
		"mission":        "Rename the greeting and keep the tests passing.",
		"workspace_root": project,
		"write_roots":    []string{project},
	}
	for k, v := range extra {
		body[k] = v
	}
	raw, err := json.Marshal(body)
	if err != nil {
		t.Fatal(err)
	}
	return raw
}

func runDelegateTool(ctx context.Context, registry *Registry, input json.RawMessage) (Result, error) {
	return registry.Execute(ctx, Request{
		ToolID: "delegate", Version: 1, Input: input, CapabilityToken: []byte("test-token"),
	})
}

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

func TestDelegateStreamsTheSubAgentTrailAndReturnsItsReport(t *testing.T) {
	requireHostSpawn(t)
	agent := installFakeAgent(t, "stream")
	registry := sessionRegistry(t)

	var rec recorder
	ctx := WithProgress(context.Background(), rec.sink())
	res, err := runDelegateTool(ctx, registry, delegateInput(t, agent.project, nil))
	if err != nil {
		t.Fatalf("delegate: %v", err)
	}
	var out map[string]any
	if err := json.Unmarshal(res.Output, &out); err != nil {
		t.Fatal(err)
	}
	if out["report"] != "Changed main.go and ran the build." {
		t.Fatalf("the final result event is the tool result: %#v", out)
	}
	if out["agent"] != "claude-code" || out["session_id"] != "sess_fake" {
		t.Fatalf("result should name the agent and its session: %#v", out)
	}
	if code, _ := out["exit_code"].(float64); code != 0 {
		t.Fatalf("exit_code=%v", out["exit_code"])
	}

	events := rec.all()
	handed := indexOf(events, func(ev ProgressEvent) bool {
		return ev.Kind == ProgressStatus && strings.Contains(ev.Text, "Handing the mission to Claude Code")
	})
	spoke := indexOf(events, func(ev ProgressEvent) bool {
		return ev.Kind == ProgressStatus && strings.Contains(ev.Text, "Reading main.go")
	})
	called := indexOf(events, func(ev ProgressEvent) bool {
		return ev.Kind == ProgressToolCall && ev.Name == "Edit"
	})
	returned := indexOf(events, func(ev ProgressEvent) bool {
		return ev.Kind == ProgressToolResult && strings.Contains(ev.Output, "main.go has been updated")
	})
	finished := indexOf(events, func(ev ProgressEvent) bool {
		return ev.Kind == ProgressStatus && strings.Contains(ev.Text, "finished the mission")
	})
	if handed < 0 || spoke < 0 || called < 0 || returned < 0 || finished < 0 {
		t.Fatalf("the trail is missing part of the run: %#v", events)
	}
	if !(handed < spoke && spoke < called && called < returned && returned < finished) {
		t.Fatalf("events reached the trail out of order: %#v", events)
	}
	for _, ev := range events {
		if ev.Via != "claude-code" {
			t.Fatalf("every mirrored event must say who did the work: %#v", ev)
		}
	}
	if strings.Contains(events[spoke].Text, "\n") {
		t.Fatalf("a status line must stay on one line: %q", events[spoke].Text)
	}

	inv := agent.invocation(t)
	if inv.Depth != "1" {
		t.Fatalf("the sub-agent must inherit %s=1, got %q", DelegateDepthEnv, inv.Depth)
	}
	if !strings.EqualFold(filepath.Clean(inv.Cwd), filepath.Clean(agent.project)) {
		t.Fatalf("the agent ran in %q, want the bound project folder %q", inv.Cwd, agent.project)
	}
	joined := strings.Join(inv.Argv, " ")
	for _, want := range []string{"-p", "--output-format=stream-json", "--verbose", "--permission-mode=acceptEdits"} {
		if !strings.Contains(joined, want) {
			t.Fatalf("argv is missing %q: %v", want, inv.Argv)
		}
	}
}

func TestDelegateArgvCarriesThePermissionMode(t *testing.T) {
	edits := claudeCodeArgv("do the thing", false)
	allowed := flagValue(t, edits, "--allowedTools")
	if strings.Contains(allowed, "Bash") {
		t.Fatalf("allow_shell:false must not allow the shell: %q", allowed)
	}
	if !strings.Contains(allowed, "Edit") || !strings.Contains(allowed, "Write") {
		t.Fatalf("allow_shell:false still edits files: %q", allowed)
	}
	denied := flagValue(t, edits, "--disallowedTools")
	for _, tool := range delegateShellTools {
		if !strings.Contains(denied, tool) {
			t.Fatalf("%s must be denied outright: %q", tool, denied)
		}
	}

	shell := claudeCodeArgv("do the thing", true)
	allowed = flagValue(t, shell, "--allowedTools")
	if !strings.Contains(allowed, "Bash") {
		t.Fatalf("allow_shell:true must widen the allowlist: %q", allowed)
	}
	if hasFlag(shell, "--disallowedTools") {
		t.Fatalf("allow_shell:true leaves nothing to deny: %v", shell)
	}
	prompt := flagValue(t, shell, "--append-system-prompt")
	if !strings.Contains(prompt, "Remedy") {
		t.Fatalf("the sub-agent must be told who started it: %q", prompt)
	}
}

func TestDelegateAllowShellWidensTheRealInvocation(t *testing.T) {
	requireHostSpawn(t)
	agent := installFakeAgent(t, "stream")
	registry := sessionRegistry(t)

	if _, err := runDelegateTool(context.Background(), registry,
		delegateInput(t, agent.project, map[string]any{"allow_shell": true})); err != nil {
		t.Fatalf("delegate: %v", err)
	}
	inv := agent.invocation(t)
	allowed := flagValue(t, inv.Argv, "--allowedTools")
	if !strings.Contains(allowed, "Bash") {
		t.Fatalf("allow_shell:true argv=%v", inv.Argv)
	}
	if hasFlag(inv.Argv, "--disallowedTools") {
		t.Fatalf("allow_shell:true argv=%v", inv.Argv)
	}
}

func TestDelegateSurfacesANonZeroExitWithTheReport(t *testing.T) {
	requireHostSpawn(t)
	agent := installFakeAgent(t, "fail")
	registry := sessionRegistry(t)

	_, err := runDelegateTool(context.Background(), registry, delegateInput(t, agent.project, nil))
	if err == nil {
		t.Fatal("a failed mission must not read as success")
	}
	if !strings.Contains(err.Error(), "exit code 2") {
		t.Fatalf("the error must carry the exit code: %v", err)
	}
	if !strings.Contains(err.Error(), "The build failed: undefined: Foo.") {
		t.Fatalf("the error must carry the agent's own report: %v", err)
	}
}

func TestDelegateCancellationKillsTheProcessTree(t *testing.T) {
	requireHostSpawn(t)
	agent := installFakeAgent(t, "hang")
	registry := sessionRegistry(t)

	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, err := runDelegateTool(ctx, registry, delegateInput(t, agent.project, nil))
		done <- err
	}()

	childBeat := filepath.Join(agent.dir, "child.beat")
	grandBeat := filepath.Join(agent.dir, "grandchild.beat")
	waitForFile(t, childBeat, 30*time.Second)
	waitForFile(t, grandBeat, 30*time.Second)
	cancel()

	select {
	case err := <-done:
		if err == nil || !errors.Is(err, context.Canceled) {
			t.Fatalf("a cancelled mission must report cancellation: %v", err)
		}
	case <-time.After(30 * time.Second):
		t.Fatal("delegate did not return after cancellation")
	}

	// Nothing in the tree may still be running: sample both heartbeats, wait,
	// and require them to be unchanged.
	time.Sleep(750 * time.Millisecond)
	childBefore := readFileForTest(t, childBeat)
	grandBefore := readFileForTest(t, grandBeat)
	time.Sleep(1500 * time.Millisecond)
	if got := readFileForTest(t, childBeat); got != childBefore {
		t.Fatalf("the sub-agent survived cancellation (%q -> %q)", childBefore, got)
	}
	if got := readFileForTest(t, grandBeat); got != grandBefore {
		t.Fatalf("a grandchild was orphaned (%q -> %q)", grandBefore, got)
	}
}

func TestDelegateTimeoutStopsTheAgent(t *testing.T) {
	requireHostSpawn(t)
	agent := installFakeAgent(t, "hang")
	registry := sessionRegistry(t)

	start := time.Now()
	_, err := runDelegateTool(context.Background(), registry,
		delegateInput(t, agent.project, map[string]any{"timeout_ms": 1500}))
	if err == nil || !strings.Contains(err.Error(), "did not finish within") {
		t.Fatalf("the timeout must be reported plainly: %v", err)
	}
	if elapsed := time.Since(start); elapsed > 30*time.Second {
		t.Fatalf("the timeout took %s to fire", elapsed)
	}
	childBeat := filepath.Join(agent.dir, "child.beat")
	before := readFileForTest(t, childBeat)
	time.Sleep(1500 * time.Millisecond)
	if got := readFileForTest(t, childBeat); got != before {
		t.Fatalf("the timed-out agent is still running (%q -> %q)", before, got)
	}
}

func TestDelegateWithoutTheAgentInstalledSaysWhatToInstall(t *testing.T) {
	registry := sessionRegistry(t)
	t.Setenv("PATH", t.TempDir())
	project := t.TempDir()

	_, err := runDelegateTool(context.Background(), registry, delegateInput(t, project, nil))
	if err == nil {
		t.Fatal("a missing agent must be reported, not assumed")
	}
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("a missing agent is an ordinary tool failure: %v", err)
	}
	for _, want := range []string{"Claude Code", "claude", "install"} {
		if !strings.Contains(err.Error(), want) {
			t.Fatalf("the error must be actionable (%q missing): %v", want, err)
		}
	}
}

func TestDelegateRefusesCodexUntilItIsWiredUp(t *testing.T) {
	registry := sessionRegistry(t)
	_, err := runDelegateTool(context.Background(), registry,
		delegateInput(t, t.TempDir(), map[string]any{"agent": "codex"}))
	if err == nil || !strings.Contains(err.Error(), "Codex") {
		t.Fatalf("codex must fail with a clear message: %v", err)
	}
	if !strings.Contains(err.Error(), "claude-code") {
		t.Fatalf("the error should point at the agent that works: %v", err)
	}
}

func TestDelegateRefusesToDelegateAgain(t *testing.T) {
	registry := sessionRegistry(t)
	agent := installFakeAgent(t, "stream")
	t.Setenv(DelegateDepthEnv, "1")

	_, err := runDelegateTool(context.Background(), registry, delegateInput(t, agent.project, nil))
	if err == nil {
		t.Fatal("a delegated agent must not be able to delegate again")
	}
	if !errors.Is(err, ErrInvalidInput) || !strings.Contains(err.Error(), DelegateDepthEnv) {
		t.Fatalf("the refusal must name why: %v", err)
	}
	if _, statErr := os.Stat(filepath.Join(agent.dir, "agent.json")); statErr == nil {
		t.Fatal("nothing may be spawned when delegation is refused")
	}
}

func TestDelegateWorkdirStaysInsideTheBoundFolder(t *testing.T) {
	project := t.TempDir()
	inside := filepath.Join(project, "service")
	if err := os.MkdirAll(inside, 0o755); err != nil {
		t.Fatal(err)
	}
	got, err := delegateWorkdir(inside, project)
	if err != nil || !strings.EqualFold(got, inside) {
		t.Fatalf("a folder inside the project is allowed: %q %v", got, err)
	}
	if got, err = delegateWorkdir("", project); err != nil || !strings.EqualFold(got, filepath.Clean(project)) {
		t.Fatalf("no cwd means the project folder: %q %v", got, err)
	}
	if _, err = delegateWorkdir(t.TempDir(), project); err == nil {
		t.Fatal("a folder outside the project must be refused")
	}
	if _, err = delegateWorkdir("service", project); err == nil {
		t.Fatal("a relative cwd must be refused")
	}
	if _, err = delegateWorkdir("", ""); err == nil {
		t.Fatal("with no bound folder there is nowhere to run")
	}
}

func TestDelegateRefusesAnEmptyMission(t *testing.T) {
	registry := sessionRegistry(t)
	raw, err := json.Marshal(map[string]any{"mission": "   ", "workspace_root": t.TempDir()})
	if err != nil {
		t.Fatal(err)
	}
	if _, err := runDelegateTool(context.Background(), registry, raw); err == nil {
		t.Fatal("an empty mission must be refused")
	}
}

func TestDelegateChildEnvironmentIsScrubbedAndMarked(t *testing.T) {
	t.Setenv("PYTHONPATH", "C:/attacker")
	t.Setenv("NODE_OPTIONS", "--require=/tmp/evil.js")
	t.Setenv("ZZ_ORDINARY", "kept")

	env := delegateChildEnv()
	for _, key := range []string{"PYTHONPATH", "NODE_OPTIONS"} {
		if _, present := env[key]; present {
			t.Fatalf("%s must not reach the sub-agent", key)
		}
	}
	if env["ZZ_ORDINARY"] != "kept" {
		t.Fatal("the sub-agent still needs a real environment")
	}
	if env["PATH"] == "" && env["Path"] == "" {
		t.Fatal("the sub-agent needs PATH to find its own tools")
	}
	if env[DelegateDepthEnv] != "1" {
		t.Fatalf("the delegation marker must be set: %q", env[DelegateDepthEnv])
	}
	if bad := modelDeniedEnvKey(env); bad != "" {
		t.Fatalf("%s survived the scrub", bad)
	}
}

func TestDelegateStreamParserReadsClaudeCodeEvents(t *testing.T) {
	lines := strings.Join([]string{
		`{"type":"system","subtype":"init","session_id":"s1","model":"m"}`,
		`{"type":"assistant","message":{"content":[{"type":"text","text":"hello"}]}}`,
		`not json at all`,
		`{"type":"result","subtype":"success","result":"done","duration_ms":12,"session_id":"s1"}`,
	}, "\n") + "\n"

	var rec recorder
	ctx := WithProgress(context.Background(), rec.sink())
	stream := consumeDelegateStream(ctx, delegateAgents["claude-code"], strings.NewReader(lines))
	if stream.report != "done" || stream.sessionID != "s1" || stream.durationMS != 12 {
		t.Fatalf("stream=%#v", stream)
	}
	if stream.isError {
		t.Fatal("a success subtype is not an error")
	}
	if len(rec.all()) != 2 {
		t.Fatalf("garbage lines must be skipped, not mirrored: %#v", rec.all())
	}
}

// ---------------------------------------------------------------------------
// helpers
// ---------------------------------------------------------------------------

// flagValue reads --flag=value (the form delegate builds) or --flag value.
func flagValue(t *testing.T, argv []string, flag string) string {
	t.Helper()
	for i, arg := range argv {
		if strings.HasPrefix(arg, flag+"=") {
			return strings.TrimPrefix(arg, flag+"=")
		}
		if arg == flag && i+1 < len(argv) {
			return argv[i+1]
		}
	}
	t.Fatalf("argv has no %s: %v", flag, argv)
	return ""
}

func hasFlag(argv []string, flag string) bool {
	for _, arg := range argv {
		if arg == flag || strings.HasPrefix(arg, flag+"=") {
			return true
		}
	}
	return false
}

func waitForFile(t *testing.T, path string, within time.Duration) {
	t.Helper()
	deadline := time.Now().Add(within)
	for time.Now().Before(deadline) {
		if _, err := os.Stat(path); err == nil {
			return
		}
		time.Sleep(50 * time.Millisecond)
	}
	t.Fatalf("%s never appeared", path)
}

func readFileForTest(t *testing.T, path string) string {
	t.Helper()
	data, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(data)
}
