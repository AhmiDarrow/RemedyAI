package tools

import (
	"context"
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
)

// shellRegistry is the bash/jobs surface. A live remedy_core is required: the
// spawn goes through the Zig authorized path, and there is no soft fallback.
func shellRegistry(t *testing.T) *Registry {
	t.Helper()
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error { return nil }))
	if err := RegisterShellTools(registry); err != nil {
		t.Fatal(err)
	}
	return registry
}

// requireHostSpawn skips when remedy_core is not built, and points the tools at
// a scratch Remedy home so nothing touches the owner's.
func requireHostSpawn(t *testing.T) string {
	t.Helper()
	core.ResetForTest()
	t.Cleanup(core.ResetForTest)
	if core.FindLibraryPath() == "" {
		t.Skip("remedy_core not built")
	}
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	return home
}

func TestShellToolDescriptorsDescribeEveryProperty(t *testing.T) {
	registry := shellRegistry(t)
	for _, id := range []string{"bash", "jobs"} {
		desc, err := registry.Latest(id)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		if desc.Risk != RiskMutation {
			t.Fatalf("%s risk=%v", id, desc.Risk)
		}
		if len(desc.Capabilities) == 0 || desc.Capabilities[0] != "process.spawn" {
			t.Fatalf("%s capabilities=%v", id, desc.Capabilities)
		}
		var schema map[string]any
		if err := json.Unmarshal(desc.InputSchema, &schema); err != nil {
			t.Fatal(err)
		}
		for name, raw := range schema["properties"].(map[string]any) {
			p, _ := raw.(map[string]any)
			if d, _ := p["description"].(string); strings.TrimSpace(d) == "" {
				t.Fatalf("%s.%s has no description", id, name)
			}
		}
	}
}

func TestBashTakesACommandStringAndWrapsItInTheHostShell(t *testing.T) {
	argv, err := platformShellArgv("echo hi && exit 3")
	if err != nil {
		t.Fatal(err)
	}
	if !filepath.IsAbs(argv[0]) {
		t.Fatalf("argv[0] must be absolute for the Zig spawn: %v", argv)
	}
	// The command survives as one argument, so the classifier that unwraps
	// shell wrappers sees the real command line rather than a hash of tokens.
	if argv[len(argv)-1] != "echo hi && exit 3" {
		t.Fatalf("command must reach the shell intact: %v", argv)
	}
	base := strings.ToLower(filepath.Base(argv[0]))
	if runtime.GOOS == "windows" {
		if base != "cmd.exe" || argv[1] != "/d" || argv[2] != "/s" || argv[3] != "/c" {
			t.Fatalf("windows wrapper=%v", argv)
		}
	} else if base != "bash" && base != "sh" {
		t.Fatalf("posix wrapper=%v", argv)
	}
}

func TestBashRefusesInterpreterPathEnvAndRelativeCwd(t *testing.T) {
	registry := shellRegistry(t)
	_, _, err := callTool(t, registry, "bash", map[string]any{
		"command": "echo hi", "env": map[string]any{"PYTHONPATH": "."},
	})
	if err == nil || !strings.Contains(err.Error(), "PYTHONPATH") {
		t.Fatalf("module-search env must be refused: %v", err)
	}
	_, _, err = callTool(t, registry, "bash", map[string]any{"command": "echo hi", "cwd": "rel/path"})
	if err == nil || !strings.Contains(err.Error(), "absolute") {
		t.Fatalf("relative cwd: %v", err)
	}
	_, _, err = callTool(t, registry, "bash", map[string]any{"command": "   "})
	if err == nil {
		t.Fatal("a blank command must be refused")
	}
}

func TestBashPropagatesExitCode(t *testing.T) {
	requireHostSpawn(t)
	registry := shellRegistry(t)
	command := "exit 3"
	if runtime.GOOS == "windows" {
		command = "echo failing 1>&2 & exit /b 3"
	}
	out := mustCall(t, registry, "bash", map[string]any{"command": command, "timeout_ms": 30000})
	if got := asInt(t, out["exit_code"]); got != 3 {
		t.Fatalf("exit_code=%d want 3 (%#v)", got, out)
	}
	if out["timed_out"] != false {
		t.Fatalf("a clean non-zero exit is not a timeout: %#v", out)
	}

	out = mustCall(t, registry, "bash", map[string]any{"command": "echo bash-ok", "timeout_ms": 30000})
	if asInt(t, out["exit_code"]) != 0 {
		t.Fatalf("exit_code: %#v", out)
	}
	if body, _ := out["stdout"].(string); !strings.Contains(body, "bash-ok") {
		t.Fatalf("stdout=%q", body)
	}
	if out["stdout_truncated"] != false {
		t.Fatalf("short output must not be marked truncated: %#v", out)
	}
}

func TestBashIsCancellable(t *testing.T) {
	requireHostSpawn(t)
	registry := shellRegistry(t)
	command := "sleep 30"
	if runtime.GOOS == "windows" {
		command = "ping -n 30 127.0.0.1 >NUL"
	}
	input, err := json.Marshal(map[string]any{"command": command, "timeout_ms": 60000})
	if err != nil {
		t.Fatal(err)
	}
	ctx, cancel := context.WithCancel(context.Background())
	done := make(chan error, 1)
	go func() {
		_, execErr := registry.Execute(ctx, Request{
			ToolID: "bash", Version: 1, Input: input, CapabilityToken: []byte("test-token"),
		})
		done <- execErr
	}()
	time.Sleep(300 * time.Millisecond)
	cancel()
	select {
	case execErr := <-done:
		if execErr == nil || !strings.Contains(execErr.Error(), "context canceled") {
			t.Fatalf("cancel must stop the command: %v", execErr)
		}
	case <-time.After(20 * time.Second):
		t.Fatal("Stop did not cancel a running command")
	}
}

func TestClipHeadTailKeepsBothEnds(t *testing.T) {
	body := make([]byte, bashOutputHead+bashOutputTail+4096)
	for i := range body {
		body[i] = 'x'
	}
	copy(body, []byte("HEAD-MARKER"))
	copy(body[len(body)-11:], []byte("TAIL-MARKER"))
	out, clipped := clipHeadTail(body)
	if !clipped {
		t.Fatal("oversized output must be marked truncated")
	}
	if !strings.HasPrefix(out, "HEAD-MARKER") || !strings.HasSuffix(out, "TAIL-MARKER") {
		t.Fatalf("head and tail must both survive: %q … %q", out[:20], out[len(out)-20:])
	}
	if !strings.Contains(out, "omitted from the middle") {
		t.Fatal("the clip must say what it dropped")
	}
	small := []byte("short")
	if out, clipped := clipHeadTail(small); clipped || out != "short" {
		t.Fatalf("small output was clipped: %q %v", out, clipped)
	}
}

func TestBackgroundJobIsListedTailedAndKilled(t *testing.T) {
	home := requireHostSpawn(t)
	registry := shellRegistry(t)
	session := "sess-jobs"
	command := "echo started; sleep 30"
	if runtime.GOOS == "windows" {
		command = "echo started & ping -n 30 127.0.0.1 >NUL"
	}
	started := mustCall(t, registry, "bash", map[string]any{
		"command": command, "background": true, "home_dir": home, "session_id": session,
	})
	jobID, _ := started["job_id"].(string)
	if jobID == "" || started["status"] != "running" {
		t.Fatalf("background start: %#v", started)
	}
	if _, err := os.Stat(filepath.Join(home, "sessions", session, "jobs", jobID+".json")); err != nil {
		t.Fatalf("job state must live under <home>/sessions/<sid>/jobs: %v", err)
	}

	listed := mustCall(t, registry, "jobs", map[string]any{
		"action": "list", "home_dir": home, "session_id": session,
	})
	rows := toMaps(t, listed["jobs"])
	if len(rows) != 1 || rows[0]["id"] != jobID || rows[0]["status"] != "running" {
		t.Fatalf("jobs list: %#v", rows)
	}

	// Tail: the child writes its first line quickly; poll rather than sleep on
	// a fixed budget so a slow host does not make this flaky.
	var output string
	for i := 0; i < 100; i++ {
		tailed := mustCall(t, registry, "jobs", map[string]any{
			"action": "tail", "job_id": jobID, "home_dir": home, "session_id": session,
		})
		output, _ = tailed["output"].(string)
		if strings.Contains(output, "started") {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if !strings.Contains(output, "started") {
		t.Fatalf("tail never saw the job's output: %q", output)
	}

	killed := mustCall(t, registry, "jobs", map[string]any{
		"action": "kill", "job_id": jobID, "home_dir": home, "session_id": session,
	})
	job, _ := killed["job"].(map[string]any)
	if job == nil || job["status"] != "killed" {
		t.Fatalf("kill: %#v", killed)
	}
	// The supervisor reaps it and the recorded outcome stays killed.
	for i := 0; i < 100; i++ {
		listed = mustCall(t, registry, "jobs", map[string]any{
			"action": "list", "home_dir": home, "session_id": session,
		})
		rows = toMaps(t, listed["jobs"])
		if len(rows) == 1 && rows[0]["ended_ms"] != nil {
			break
		}
		time.Sleep(100 * time.Millisecond)
	}
	if rows[0]["status"] != "killed" {
		t.Fatalf("a killed job must stay killed: %#v", rows[0])
	}
}

func TestJobsRefusesUnknownIdsAndActions(t *testing.T) {
	home := t.TempDir()
	registry := shellRegistry(t)
	_, _, err := callTool(t, registry, "jobs", map[string]any{
		"action": "tail", "job_id": "job_missing", "home_dir": home, "session_id": "s1",
	})
	if err == nil || !strings.Contains(err.Error(), "action list") {
		t.Fatalf("unknown job must point at list: %v", err)
	}
	_, _, err = callTool(t, registry, "jobs", map[string]any{
		"action": "kill", "home_dir": home, "session_id": "s1",
	})
	if err == nil || !strings.Contains(err.Error(), "job_id is required") {
		t.Fatalf("missing job_id: %v", err)
	}
	// A traversal id can never become a path.
	_, _, err = callTool(t, registry, "jobs", map[string]any{
		"action": "tail", "job_id": "../../secret", "home_dir": home, "session_id": "s1",
	})
	if err == nil {
		t.Fatal("a traversal job id must be refused")
	}
	// An empty session has an empty list, not an error.
	out := mustCall(t, registry, "jobs", map[string]any{
		"action": "list", "home_dir": home, "session_id": "s1",
	})
	if asInt(t, out["count"]) != 0 {
		t.Fatalf("empty list: %#v", out)
	}
	_, _, err = callTool(t, registry, "jobs", map[string]any{
		"action": "list", "home_dir": home,
	})
	if err == nil || !strings.Contains(err.Error(), "session") {
		t.Fatalf("jobs without a session: %v", err)
	}
}
