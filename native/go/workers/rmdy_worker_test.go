package workers

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"os/exec"
	"path/filepath"
	"runtime"
	"strconv"
	"strings"
	"sync"
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
	// The repo .venv carries pydantic/yaml so prompt.assemble (the attach
	// probe) works; a bare PATH python usually does not.
	if venv := repoVenvPython(); venv != "" {
		candidates = append(candidates, venv)
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

func TestResolveDefaultWorkspaceUsesDocumentsRemedy(t *testing.T) {
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
	got := resolveDefaultWorkspace(map[string]string{}, install)
	if got == "" || looksLikeInstallDir(got) {
		t.Fatalf("workspace = %q (must not be empty or install)", got)
	}
	if !strings.Contains(filepath.ToSlash(got), "/Documents/Remedy") &&
		!strings.Contains(filepath.ToSlash(got), "/.remedy/workspace") {
		t.Fatalf("workspace = %q want Documents/Remedy or .remedy/workspace", got)
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
		HomeDir:       t.TempDir(),
		Cwd:           root,
		PythonArgv:    argv,
		AttachTimeout: 60 * time.Second,
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

func TestManagedVoicePythonAndStoreStub(t *testing.T) {
	t.Setenv("REMEDY_VOICE_PYTHON", "")
	t.Setenv("REMEDY_HOME", t.TempDir())
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	var cand string
	if runtime.GOOS == "windows" {
		cand = filepath.Join(home, "voice", "runtime", "python", "python.exe")
	} else {
		cand = filepath.Join(home, "voice", "runtime", "python", "bin", "python3")
	}
	if err := os.MkdirAll(filepath.Dir(cand), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cand, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	got := managedVoicePython()
	abs, _ := filepath.Abs(cand)
	if got != abs {
		t.Fatalf("managed=%q want=%q", got, abs)
	}
	stub := `C:\Users\x\AppData\Local\Microsoft\WindowsApps\python.exe`
	if !isWindowsStorePythonStub(stub) {
		t.Fatalf("expected WindowsApps stub rejection for %q", stub)
	}
	if isWindowsStorePythonStub(cand) {
		t.Fatalf("real cand should not look like a Store stub")
	}
}

func TestPathUnderManagedAndExtractSkip(t *testing.T) {
	root := t.TempDir()
	inside := filepath.Join(root, "python", "python.exe")
	if !pathUnderManaged(inside, root) {
		t.Fatal("inside should be under root")
	}
	outside := filepath.Join(root, "..", "escape")
	if pathUnderManaged(outside, root) {
		t.Fatal("escape must be rejected")
	}
}

func TestEnsureManagedPythonUsesExisting(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_VOICE_PYTHON", "")
	t.Setenv("REMEDY_SKIP_MANAGED_PYTHON_DOWNLOAD", "1")
	var cand string
	if runtime.GOOS == "windows" {
		cand = filepath.Join(home, "voice", "runtime", "python", "python.exe")
	} else {
		cand = filepath.Join(home, "voice", "runtime", "python", "bin", "python3")
	}
	if err := os.MkdirAll(filepath.Dir(cand), 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(cand, []byte("x"), 0o755); err != nil {
		t.Fatal(err)
	}
	got, err := ensureManagedPython()
	if err != nil {
		t.Fatal(err)
	}
	abs, _ := filepath.Abs(cand)
	if got != abs {
		t.Fatalf("got=%q want=%q", got, abs)
	}
}

func TestEnsureManagedPythonSkipDownload(t *testing.T) {
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_VOICE_PYTHON", "")
	t.Setenv("REMEDY_SKIP_MANAGED_PYTHON_DOWNLOAD", "1")
	_, err := ensureManagedPython()
	if err == nil {
		t.Fatal("expected error when missing and download skipped")
	}
}

// execWaitingProcessStarter is execProcessStarter plus a Wait: the attach path
// diagnoses a worker that exits before dialing only when the starter can
// report an exit code (production ZigProcessStarter always can).
func execWaitingProcessStarter(t *testing.T) ProcessStarter {
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
		type waitResult struct {
			code uint32
			err  error
		}
		done := make(chan waitResult, 1)
		go func() {
			err := cmd.Wait()
			code := cmd.ProcessState.ExitCode()
			if code < 0 {
				done <- waitResult{0, err}
				return
			}
			done <- waitResult{uint32(code), nil}
		}()
		var once sync.Once
		var result waitResult
		wait := func() (uint32, error) {
			once.Do(func() { result = <-done })
			return result.code, result.err
		}
		return &StartedProcess{
			PID: uint32(cmd.Process.Pid),
			Cleanup: func() error {
				_ = cmd.Process.Kill()
				_, _ = wait()
				return nil
			},
			Wait: wait,
		}, nil
	}
}

func TestWorkerExitErrorNamesMissingDependencies(t *testing.T) {
	err := workerExitError(ExitMissingDependencies)
	if !errors.Is(err, ErrWorkerAttachRequired) || !errors.Is(err, ErrWorkerMissingDeps) {
		t.Fatalf("err=%v must wrap both attach-required and missing-deps", err)
	}
	if !strings.Contains(err.Error(), "reinstall") {
		t.Fatalf("err=%v must tell the operator to reinstall", err)
	}
	other := workerExitError(3)
	if errors.Is(other, ErrWorkerMissingDeps) {
		t.Fatalf("exit 3 is not a dependency failure: %v", other)
	}
}

// TestAttachDiagnosesMissingWorkerDependencies runs the real worker entry on an
// interpreter whose sys.path has neither site-packages (-S) nor a staged
// dependency directory, which is exactly a packaged install with a broken
// rmdy-deps. The attach must fail with the reinstall diagnosis rather than a
// bare timeout.
func TestAttachDiagnosesMissingWorkerDependencies(t *testing.T) {
	root := repoRoot(t)
	python := testPythonArgv(t)[0]
	empty := t.TempDir()
	t.Setenv(envRMDYDeps, empty)

	src := filepath.ToSlash(filepath.Join(root, "src"))
	program := "import sys; sys.path.insert(0, " + strconv.Quote(src) + ")\n" +
		"from remedy.runtime.rmdy_tool_worker import main\n" +
		"raise SystemExit(main())\n"

	ctx, cancel := context.WithTimeout(context.Background(), 90*time.Second)
	defer cancel()
	_, err := StartRMDYToolWorker(ctx, RMDYToolOptions{
		HomeDir: t.TempDir(),
		Cwd:     root,
		// -S: no site-packages. -E: no PYTHONPATH. Only the deps directory
		// could supply pydantic/yaml, and it is empty.
		PythonArgv:    []string{python, "-S", "-E", "-c", program},
		AttachTimeout: 60 * time.Second,
		StartProcess:  execWaitingProcessStarter(t),
	})
	if err == nil {
		t.Fatal("expected attach to fail without the worker dependency closure")
	}
	if !errors.Is(err, ErrWorkerMissingDeps) {
		t.Fatalf("err=%v want %v", err, ErrWorkerMissingDeps)
	}
}

func TestResolveRMDYDepsFailClosed(t *testing.T) {
	t.Setenv(envRMDYDeps, filepath.Join(t.TempDir(), "missing"))
	if _, _, err := resolveRMDYDeps(); !errors.Is(err, ErrWorkerAttachRequired) {
		t.Fatalf("err=%v want fail-closed attach error", err)
	}
	// A file is not a directory.
	file := filepath.Join(t.TempDir(), "rmdy-deps")
	if err := os.WriteFile(file, []byte("x"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(envRMDYDeps, file)
	if _, _, err := resolveRMDYDeps(); err == nil {
		t.Fatal("expected a file at REMEDY_RMDY_DEPS to fail closed")
	}
}

func TestResolveRMDYDepsFoundBesideTheZipapp(t *testing.T) {
	dir := t.TempDir()
	pyz := filepath.Join(dir, "rmdy_tool_worker.pyz")
	if err := os.WriteFile(pyz, []byte("PK\x03\x04"), 0o600); err != nil {
		t.Fatal(err)
	}
	deps := filepath.Join(dir, depsDirName)
	if err := os.MkdirAll(deps, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Setenv(envRMDYDeps, "")
	t.Setenv(envRMDYPyz, pyz)
	got, ok, err := resolveRMDYDeps()
	if err != nil || !ok {
		t.Fatalf("ok=%v err=%v", ok, err)
	}
	abs, _ := filepath.Abs(deps)
	if got != abs {
		t.Fatalf("got=%q want=%q", got, abs)
	}

	env := map[string]string{}
	if err := applyWorkerDepsEnv(env); err != nil {
		t.Fatal(err)
	}
	if env[envRMDYDeps] != abs {
		t.Fatalf("env=%q want=%q", env[envRMDYDeps], abs)
	}
	// An explicit value survives.
	env[envRMDYDeps] = "keep"
	if err := applyWorkerDepsEnv(env); err != nil {
		t.Fatal(err)
	}
	if env[envRMDYDeps] != "keep" {
		t.Fatalf("explicit REMEDY_RMDY_DEPS overwritten: %q", env[envRMDYDeps])
	}
}

// TestDefaultPythonWorkerArgvPrefersRepoVenv guards the dev path: a stale
// zipapp under dist/ must not push the launcher onto a PATH interpreter that
// has none of the worker dependencies.
func TestDefaultPythonWorkerArgvPrefersRepoVenv(t *testing.T) {
	venv := repoVenvPython()
	if venv == "" {
		t.Skip("no repo .venv in this checkout")
	}
	dir := t.TempDir()
	pyz := filepath.Join(dir, "rmdy_tool_worker.pyz")
	if err := os.WriteFile(pyz, []byte("PK\x03\x04"), 0o600); err != nil {
		t.Fatal(err)
	}
	t.Setenv(envPython, "")
	t.Setenv(envRMDYPyz, pyz)
	argv, err := defaultPythonWorkerArgv()
	if err != nil {
		t.Fatal(err)
	}
	if argv[0] != venv {
		t.Fatalf("argv[0]=%q want repo venv %q", argv[0], venv)
	}
	if PythonWorkerNeedsDownload() {
		t.Fatal("a repo .venv must never trigger the managed CPython download")
	}
}

// TestAttachSucceedsWithStagedWorkerDependencies is the packaged path end to
// end: the zipapp plus its staged dependency directory on an interpreter with
// no site-packages (-S) and no PYTHONPATH (-E). The default attach probe runs
// prompt.assemble, so a successful attach proves the whole import chain
// resolves from the staged closure. Skipped until packaging has run.
func TestAttachSucceedsWithStagedWorkerDependencies(t *testing.T) {
	root := repoRoot(t)
	pyz := filepath.Join(root, "dist", "rmdy_tool_worker.pyz")
	deps := filepath.Join(root, "dist", depsDirName)
	if st, err := os.Stat(pyz); err != nil || st.IsDir() {
		t.Skip("run scripts/build_rmdy_worker.py to stage the zipapp")
	}
	manifest, err := os.ReadFile(filepath.Join(deps, "rmdy_deps.json"))
	if err != nil {
		t.Skip("run scripts/build_rmdy_worker.py to stage the deps directory")
	}
	var staged struct {
		PythonVersion string `json:"python_version"`
	}
	if err := json.Unmarshal(manifest, &staged); err != nil {
		t.Fatal(err)
	}
	python := testPythonArgv(t)[0]
	out, err := exec.Command(python, "-c", "import sys;print('%d.%d'%sys.version_info[:2])").Output()
	if err != nil {
		t.Fatalf("interpreter version: %v", err)
	}
	if strings.TrimSpace(string(out)) != staged.PythonVersion {
		t.Skipf("staged wheels target CPython %s, test interpreter is %s",
			staged.PythonVersion, strings.TrimSpace(string(out)))
	}

	t.Setenv(envRMDYDeps, deps)
	ctx, cancel := context.WithTimeout(context.Background(), 3*time.Minute)
	defer cancel()
	session, err := StartRMDYToolWorker(ctx, RMDYToolOptions{
		HomeDir:       t.TempDir(),
		Cwd:           root,
		PythonArgv:    []string{python, "-S", "-E", pyz},
		AttachTimeout: 2 * time.Minute,
		StartProcess:  execWaitingProcessStarter(t),
	})
	if err != nil {
		t.Fatalf("packaged worker attach failed: %v", err)
	}
	defer session.Close()
}
