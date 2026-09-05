package workers

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

func testPythonArgv(t *testing.T) []string {
	t.Helper()
	candidates := []string{}
	if p := os.Getenv("REMEDY_PYTHON"); p != "" {
		candidates = append(candidates, p)
	}
	candidates = append(candidates, "python", "python3")
	var py string
	for _, name := range candidates {
		if filepath.IsAbs(name) {
			if st, err := os.Stat(name); err == nil && !st.IsDir() {
				py = name
				break
			}
			continue
		}
		if p, err := exec.LookPath(name); err == nil {
			py = p
			break
		}
	}
	if py == "" {
		t.Skip("python interpreter not found")
	}
	abs, err := filepath.Abs(py)
	if err != nil {
		t.Fatal(err)
	}
	return []string{abs, "-m", "remedy.runtime.rmdy_tool_worker"}
}

func execProcessStarter(t *testing.T) ProcessStarter {
	t.Helper()
	return func(ctx context.Context, argv []string, env map[string]string, cwd string) (*StartedProcess, error) {
		cmd := exec.CommandContext(ctx, argv[0], argv[1:]...)
		if cwd != "" {
			cmd.Dir = cwd
		}
		cmd.Env = make([]string, 0, len(env))
		for k, v := range env {
			cmd.Env = append(cmd.Env, k+"="+v)
		}
		cmd.Stderr = os.Stderr
		if err := cmd.Start(); err != nil {
			return nil, err
		}
		pid := uint32(cmd.Process.Pid)
		return &StartedProcess{
			PID: pid,
			Cleanup: func() error {
				_ = cmd.Process.Kill()
				_, _ = cmd.Process.Wait()
				return nil
			},
		}, nil
	}
}

func repoRoot(t *testing.T) string {
	t.Helper()
	wd, err := os.Getwd()
	if err != nil {
		t.Fatal(err)
	}
	dir := wd
	for {
		if _, err := os.Stat(filepath.Join(dir, "pyproject.toml")); err == nil {
			return dir
		}
		parent := filepath.Dir(dir)
		if parent == dir {
			t.Fatal("repo root not found")
		}
		dir = parent
	}
}

func TestResolveDefaultWorkspaceNeverInstallDir(t *testing.T) {
	home := t.TempDir()
	install := t.TempDir()
	if err := os.WriteFile(filepath.Join(install, "remedy-runtime.exe"), []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(install, "webui"), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.MkdirAll(filepath.Join(install, "windows"), 0o755); err != nil {
		t.Fatal(err)
	}
	if !looksLikeInstallDir(install) {
		t.Fatal("expected install markers to match")
	}
	proj := filepath.Join(home, "MyProject")
	if err := os.MkdirAll(proj, 0o755); err != nil {
		t.Fatal(err)
	}
	// TOML string with forward slashes — Windows Path accepts them.
	cfgLine := "project_path = \"" + filepath.ToSlash(proj) + "\"\n"
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(cfgLine), 0o600); err != nil {
		t.Fatal(err)
	}
	env := map[string]string{"REMEDY_HOME": home}
	got := resolveDefaultWorkspace(env, install)
	if filepath.Clean(got) != filepath.Clean(proj) {
		t.Fatalf("workspace = %q want project %q (must not be install %q)", got, proj, install)
	}
}

func TestProjectPathFromConfigUnescapesTOMLBackslashes(t *testing.T) {
	home := t.TempDir()
	proj := filepath.Join(home, "EscapedProj")
	if err := os.MkdirAll(proj, 0o755); err != nil {
		t.Fatal(err)
	}
	// Simulate serializeTOMLValue / json.Marshal style escaping.
	escaped := strings.ReplaceAll(proj, `\`, `\\`)
	cfg := "project_path = \"" + escaped + "\"\n"
	if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(cfg), 0o600); err != nil {
		t.Fatal(err)
	}
	got := projectPathFromConfig(home)
	if filepath.Clean(got) != filepath.Clean(proj) {
		t.Fatalf("projectPathFromConfig = %q want %q", got, proj)
	}
}

func TestStartRMDYToolWorkerAttachAndRoundTrip(t *testing.T) {
	root := repoRoot(t)
	argv := testPythonArgv(t)
	ctx, cancel := context.WithTimeout(context.Background(), 30*time.Second)
	defer cancel()

	srcPath := filepath.Join(root, "src")
	starter := execProcessStarter(t)
	wrapped := ProcessStarter(func(ctx context.Context, argv []string, env map[string]string, cwd string) (*StartedProcess, error) {
		if env == nil {
			env = map[string]string{}
		}
		env["PYTHONPATH"] = srcPath
		env["REMEDY_WORKSPACE"] = root
		return starter(ctx, argv, env, cwd)
	})
	session, err := StartRMDYToolWorker(ctx, RMDYToolOptions{
		Cwd:           root,
		PythonArgv:    argv,
		AttachTimeout: 20 * time.Second,
		StartProcess:  wrapped,
	})
	if err != nil {
		t.Fatal(err)
	}
	defer session.Close()

	reg := tools.NewRegistry()
	if err := tools.RegisterPythonWorkerTools(reg, session.Client); err != nil {
		t.Fatal(err)
	}

	result, err := reg.Execute(context.Background(), tools.Request{
		ToolID:  "text.slugify",
		Version: 1,
		Input:   json.RawMessage(`{"text":"Hello Worker Attach"}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	var out struct {
		Slug string `json:"slug"`
	}
	if err := json.Unmarshal(result.Output, &out); err != nil {
		t.Fatal(err)
	}
	if out.Slug != "hello-worker-attach" {
		t.Fatalf("slug=%q", out.Slug)
	}

	// Product tool: workspace.list against the repo root.
	list, err := reg.Execute(context.Background(), tools.Request{
		ToolID:  "workspace.list",
		Version: 1,
		Input:   json.RawMessage(`{"path":".","limit":5}`),
	})
	if err != nil {
		t.Fatal(err)
	}
	var listOut struct {
		Total   int `json:"total"`
		Entries []struct {
			Name string `json:"name"`
			Kind string `json:"kind"`
		} `json:"entries"`
	}
	if err := json.Unmarshal(list.Output, &listOut); err != nil {
		t.Fatal(err)
	}
	if listOut.Total < 1 || len(listOut.Entries) < 1 {
		t.Fatalf("workspace.list empty: %+v", listOut)
	}

	// Health frame still works on the same caller.
	var corr [16]byte
	corr[0] = 7
	frame, err := session.Client.Call(context.Background(), protocol.Frame{
		Kind:          protocol.KindHealth,
		CorrelationID: corr,
	})
	if err != nil {
		t.Fatal(err)
	}
	if frame.Kind != protocol.KindHealth {
		t.Fatalf("health kind=%d", frame.Kind)
	}
}

func TestResolveRMDYPyzFailClosed(t *testing.T) {
	t.Setenv(envRMDYPyz, filepath.Join(t.TempDir(), "missing.pyz"))
	_, _, err := resolveRMDYPyz()
	if err == nil {
		t.Fatal("expected missing REMEDY_RMDY_PYZ to fail closed")
	}
	if !errors.Is(err, ErrWorkerAttachRequired) {
		t.Fatalf("err=%v", err)
	}
}

func TestResolveRMDYPyzConfigured(t *testing.T) {
	dir := t.TempDir()
	pyz := filepath.Join(dir, "rmdy_tool_worker.pyz")
	if err := os.WriteFile(pyz, []byte("PK\x03\x04"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv(envRMDYPyz, pyz)
	got, ok, err := resolveRMDYPyz()
	if err != nil || !ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
	abs, _ := filepath.Abs(pyz)
	if got != abs {
		t.Fatalf("got=%q want=%q", got, abs)
	}
}

func TestDefaultPythonWorkerArgvPrefersPyz(t *testing.T) {
	py := testPythonArgv(t)[0]
	dir := t.TempDir()
	pyz := filepath.Join(dir, "rmdy_tool_worker.pyz")
	if err := os.WriteFile(pyz, []byte("PK\x03\x04"), 0o644); err != nil {
		t.Fatal(err)
	}
	t.Setenv(envPython, py)
	t.Setenv(envRMDYPyz, pyz)
	argv, err := defaultPythonWorkerArgv()
	if err != nil {
		t.Fatal(err)
	}
	if len(argv) != 2 || argv[0] != py {
		t.Fatalf("argv=%v", argv)
	}
	abs, _ := filepath.Abs(pyz)
	if argv[1] != abs {
		t.Fatalf("argv[1]=%q want=%q", argv[1], abs)
	}
}
