package workers

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"encoding/json"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"sync"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/ipc"
	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	envRMDYEndpoint = "REMEDY_RMDY_ENDPOINT"
	envPython       = "REMEDY_PYTHON"
	envRMDYPyz      = "REMEDY_RMDY_PYZ"
	envRMDYDeps     = "REMEDY_RMDY_DEPS"
	defaultAttach   = 15 * time.Second
)

// depsDirName is the dependency directory staged beside the zipapp by
// scripts/build_rmdy_worker.py. The zipapp carries src/remedy only; pydantic
// and PyYAML ship here because their compiled extensions cannot be imported
// from inside an archive.
const depsDirName = "rmdy-deps"

// ExitMissingDependencies is the worker exit code for an unimportable
// third-party closure (sysexits EX_CONFIG). Must match
// remedy.runtime.rmdy_tool_worker.EXIT_MISSING_DEPENDENCIES.
const ExitMissingDependencies = 78

// ErrWorkerMissingDeps is the operator-facing diagnosis for that exit code.
var ErrWorkerMissingDeps = errors.New("python worker is missing its dependencies — reinstall")

// ErrWorkerAttachRequired is returned when the RMDY tool worker cannot be
// supervised or attached. Serve must fail closed — never pretend tools exist.
var ErrWorkerAttachRequired = errors.New("RMDY tool worker attach required")

// StartedProcess is one supervised child.
type StartedProcess struct {
	PID     uint32
	Handle  uint64 // Zig process handle; 0 for non-Zig starters
	Cleanup func() error
	// Wait blocks until the child exits and returns its exit code. Optional;
	// when nil the supervisor relies on the IPC connection closing.
	Wait func() (uint32, error)
}

// ErrProcessClosed is returned by StartedProcess.Wait once Cleanup ran.
var ErrProcessClosed = errors.New("process handle closed")

// attachProbeDefault is the tool the attach probe executes. prompt.assemble
// exercises the full import chain (config, models, memory) so a packaged
// worker missing a dependency fails at startup with the traceback in the
// worker log rather than on the first chat turn.
const attachProbeDefault = "prompt.assemble"

// ProcessStarter starts argv with the given environment. Production uses Zig
// remedy_core authorized spawn (no os/exec). Tests may inject os/exec.
type ProcessStarter func(ctx context.Context, argv []string, env map[string]string, cwd string) (*StartedProcess, error)

// RMDYToolOptions configures supervised Python tool-worker attach.
type RMDYToolOptions struct {
	HomeDir       string
	Cwd           string
	PythonArgv    []string // default: absolute python -m remedy.runtime.rmdy_tool_worker
	Endpoint      string   // default: platform IPC path with remedy- prefix
	AttachTimeout time.Duration
	StartProcess  ProcessStarter // default: ZigProcessStarter
	// AttachProbeTool is executed once after the worker dials in; a failure
	// aborts the attach. Empty selects attachProbeDefault (prompt.assemble);
	// "none" skips the tool probe (the health frame is always checked).
	AttachProbeTool string
}

// RMDYToolSession is a live FrameCaller backed by a supervised Python worker.
type RMDYToolSession struct {
	Client   *ipc.Client
	Endpoint string
	PID      uint32

	listener net.Listener
	proc     *StartedProcess
	cancel   context.CancelFunc

	closeOnce sync.Once
	closeErr  error
}

// Close tears down the IPC client, listener, and child process. It runs once;
// the fields stay set so the supervisor may read Client / proc concurrently
// (closing the client is what wakes its Done channel).
func (s *RMDYToolSession) Close() error {
	if s == nil {
		return nil
	}
	s.closeOnce.Do(func() {
		var joined error
		if s.cancel != nil {
			s.cancel()
		}
		if s.Client != nil {
			joined = errors.Join(joined, s.Client.Close())
		}
		if s.listener != nil {
			joined = errors.Join(joined, s.listener.Close())
		}
		if s.proc != nil && s.proc.Cleanup != nil {
			joined = errors.Join(joined, s.proc.Cleanup())
		}
		s.closeErr = joined
	})
	return s.closeErr
}

// ZigProcessStarter spawns via remedy_core authorized hidden process.
// Windows/Linux: Zig host (dlopen libremedy_core + authorized spawn). Fail
// closed on missing library, ABI mismatch, or unsupported GOOS — no os/exec.
func ZigProcessStarter(home string) ProcessStarter {
	return func(_ context.Context, argv []string, env map[string]string, cwd string) (*StartedProcess, error) {
		key, err := secret.EnsureHostSigningKey(home)
		if err != nil {
			return nil, err
		}
		if err := core.EnsureSigningKey(key); err != nil {
			return nil, err
		}
		_ = core.WriteJailSetRoots(nil)
		token, nowMS, err := core.IssueProcessSpawnToken(argv, env, false, false)
		if err != nil {
			return nil, err
		}
		pid, handle, err := core.ProcessSpawnAuthorized(argv, cwd, env, false, token, "", "", false, nowMS)
		if err != nil {
			return nil, err
		}
		// handleMu serializes ProcessWait against ProcessClose: the Zig side
		// frees the handle on close, so a poll after close would be a
		// use-after-free.
		var handleMu sync.Mutex
		closed := false
		return &StartedProcess{
			PID:    pid,
			Handle: handle,
			Cleanup: func() error {
				handleMu.Lock()
				defer handleMu.Unlock()
				if closed {
					return nil
				}
				closed = true
				var joined error
				if pid != 0 {
					joined = errors.Join(joined, core.ProcessKillTree(pid))
				}
				joined = errors.Join(joined, core.ProcessClose(handle))
				return joined
			},
			Wait: func() (uint32, error) {
				for {
					handleMu.Lock()
					if closed {
						handleMu.Unlock()
						return 0, ErrProcessClosed
					}
					code, err := core.ProcessWait(handle, 500)
					handleMu.Unlock()
					if err != nil {
						return 0, err
					}
					if code != nil {
						return *code, nil
					}
				}
			},
		}, nil
	}
}

// StartRMDYToolWorker listens on an IPC endpoint, spawns the Python worker, and
// returns an ipc.Client FrameCaller after the worker dials in.
func StartRMDYToolWorker(ctx context.Context, opts RMDYToolOptions) (*RMDYToolSession, error) {
	if ctx == nil {
		ctx = context.Background()
	}
	timeout := opts.AttachTimeout
	if timeout <= 0 {
		timeout = defaultAttach
	}
	endpoint := strings.TrimSpace(opts.Endpoint)
	if endpoint == "" {
		var err error
		endpoint, err = defaultToolEndpoint()
		if err != nil {
			return nil, fmt.Errorf("%w: %v", ErrWorkerAttachRequired, err)
		}
	}
	// Ensure managed-python download lands under the same home the worker uses.
	if home := strings.TrimSpace(opts.HomeDir); home != "" {
		if strings.TrimSpace(os.Getenv("REMEDY_HOME")) == "" {
			_ = os.Setenv("REMEDY_HOME", home)
		}
	}
	argv := opts.PythonArgv
	if len(argv) == 0 {
		var err error
		argv, err = defaultPythonWorkerArgv()
		if err != nil {
			return nil, fmt.Errorf("%w: %v", ErrWorkerAttachRequired, err)
		}
	}
	if !filepath.IsAbs(argv[0]) {
		return nil, fmt.Errorf("%w: python argv[0] must be absolute (got %q)", ErrWorkerAttachRequired, argv[0])
	}
	starter := opts.StartProcess
	if starter == nil {
		starter = ZigProcessStarter(opts.HomeDir)
	}

	listener, err := ipc.Listen(endpoint)
	if err != nil {
		return nil, fmt.Errorf("%w: listen %s: %v", ErrWorkerAttachRequired, endpoint, err)
	}

	sessionCtx, cancel := context.WithCancel(ctx)
	session := &RMDYToolSession{
		Endpoint: endpoint,
		listener: listener,
		cancel:   cancel,
	}

	env := inheritEnv()
	env[envRMDYEndpoint] = endpoint
	if home := strings.TrimSpace(opts.HomeDir); home != "" {
		env["REMEDY_HOME"] = home
	}
	enrichWorkerEnv(env, opts.Cwd)
	if err := applyWorkerDepsEnv(env); err != nil {
		_ = session.Close()
		return nil, err
	}

	proc, err := starter(sessionCtx, argv, env, opts.Cwd)
	if err != nil {
		_ = session.Close()
		return nil, fmt.Errorf("%w: spawn worker: %v", ErrWorkerAttachRequired, err)
	}
	session.proc = proc
	if proc != nil {
		session.PID = proc.PID
	}
	exited := watchProcessExit(proc)

	acceptCtx, acceptCancel := context.WithTimeout(sessionCtx, timeout)
	defer acceptCancel()

	type acceptResult struct {
		conn net.Conn
		err  error
	}
	ch := make(chan acceptResult, 1)
	go func() {
		conn, err := listener.Accept()
		ch <- acceptResult{conn, err}
	}()

	var conn net.Conn
	select {
	case <-acceptCtx.Done():
		// The child usually died first; read its code before Close kills it.
		if code, ok := awaitProcessExit(exited, time.Second); ok {
			_ = session.Close()
			return nil, workerExitError(code)
		}
		_ = session.Close()
		return nil, fmt.Errorf("%w: worker did not dial %s within %s", ErrWorkerAttachRequired, endpoint, timeout)
	case code := <-exited:
		_ = session.Close()
		return nil, workerExitError(code)
	case res := <-ch:
		if res.err != nil {
			_ = session.Close()
			return nil, fmt.Errorf("%w: accept: %v", ErrWorkerAttachRequired, res.err)
		}
		conn = res.conn
	}

	session.Client = ipc.NewClient(conn)
	if err := probeWorker(sessionCtx, session.Client, opts, timeout); err != nil {
		_ = session.Close()
		return nil, fmt.Errorf("%w: attach probe: %v", ErrWorkerAttachRequired, err)
	}
	return session, nil
}

// probeWorker checks the health frame and runs the attach probe tool. The
// probe request is Go-bound so home_dir is honoured by the worker.
func probeWorker(ctx context.Context, client *ipc.Client, opts RMDYToolOptions, timeout time.Duration) error {
	probeCtx, cancel := context.WithTimeout(ctx, timeout)
	defer cancel()
	var corr [16]byte
	if _, err := rand.Read(corr[:]); err != nil {
		return err
	}
	health, err := client.Call(probeCtx, protocol.Frame{Kind: protocol.KindHealth, CorrelationID: corr})
	if err != nil {
		return fmt.Errorf("health: %w", err)
	}
	if health.Kind != protocol.KindHealth {
		return fmt.Errorf("health: unexpected frame kind %d", health.Kind)
	}
	var status struct {
		Protocol int  `json:"protocol"`
		Ready    bool `json:"ready"`
	}
	if err := json.Unmarshal(health.Payload, &status); err != nil {
		return fmt.Errorf("health: %w", err)
	}
	if status.Protocol != ProtocolVersion || !status.Ready {
		return fmt.Errorf("health: protocol=%d ready=%v", status.Protocol, status.Ready)
	}

	tool := strings.TrimSpace(opts.AttachProbeTool)
	if tool == "" {
		tool = attachProbeDefault
	}
	if strings.EqualFold(tool, "none") {
		return nil
	}
	input := map[string]any{"_go_bound": true}
	switch tool {
	case "prompt.assemble":
		input["message"] = "attach probe"
		input["session_id"] = "rmdy-attach-probe"
		input["chat_mode"] = true
		if home := strings.TrimSpace(opts.HomeDir); home != "" {
			input["home_dir"] = home
		}
	case "text.slugify":
		input["text"] = "attach probe"
	}
	raw, err := json.Marshal(map[string]any{"tool_id": tool, "version": 1, "input": input, "_go_bound": true})
	if err != nil {
		return err
	}
	if _, err := rand.Read(corr[:]); err != nil {
		return err
	}
	res, err := client.Call(probeCtx, protocol.Frame{Kind: protocol.KindToolRequest, CorrelationID: corr, Payload: raw})
	if err != nil {
		return fmt.Errorf("%s: %w", tool, err)
	}
	if res.Flags&1 != 0 {
		return fmt.Errorf("%s: transport error: %s", tool, string(res.Payload))
	}
	var result struct {
		OK    bool   `json:"ok"`
		Error string `json:"error"`
	}
	if err := json.Unmarshal(res.Payload, &result); err != nil {
		return fmt.Errorf("%s: %w", tool, err)
	}
	if !result.OK {
		// The worker logged the traceback to stderr and rmdy_worker.log.
		return fmt.Errorf("%s failed in worker: %s (see <REMEDY_HOME>/logs/rmdy_worker.log)", tool, result.Error)
	}
	return nil
}

// watchProcessExit reports the child's exit code once, when the starter can
// wait on it. A worker that dies before dialing — a packaged install whose
// dependency directory is missing exits ExitMissingDependencies immediately —
// is then diagnosed at once instead of after the whole attach timeout. The
// goroutine ends when the process exits or the handle is closed.
func watchProcessExit(proc *StartedProcess) <-chan uint32 {
	if proc == nil || proc.Wait == nil {
		return nil
	}
	ch := make(chan uint32, 1)
	go func() {
		code, err := proc.Wait()
		if err != nil {
			return
		}
		ch <- code
	}()
	return ch
}

// awaitProcessExit waits up to d for a pending exit code.
func awaitProcessExit(exited <-chan uint32, d time.Duration) (uint32, bool) {
	if exited == nil {
		return 0, false
	}
	timer := time.NewTimer(d)
	defer timer.Stop()
	select {
	case code := <-exited:
		return code, true
	case <-timer.C:
		return 0, false
	}
}

// workerExitError explains a worker that exited before the attach finished.
func workerExitError(code uint32) error {
	if code == ExitMissingDependencies {
		return fmt.Errorf("%w: %w (see <REMEDY_HOME>/logs/rmdy_worker.log)", ErrWorkerAttachRequired, ErrWorkerMissingDeps)
	}
	return fmt.Errorf("%w: worker exited with code %d before attaching (see <REMEDY_HOME>/logs/rmdy_worker.log)", ErrWorkerAttachRequired, code)
}

// applyWorkerDepsEnv points the worker at its staged dependency directory.
// An inherited or caller-set value wins; otherwise the directory is discovered
// beside the zipapp. Nothing is set in a dev checkout without a staged
// directory — there the venv already provides the closure.
func applyWorkerDepsEnv(env map[string]string) error {
	if env == nil {
		return nil
	}
	if strings.TrimSpace(env[envRMDYDeps]) != "" {
		return nil
	}
	deps, ok, err := resolveRMDYDeps()
	if err != nil {
		return err
	}
	if ok {
		env[envRMDYDeps] = deps
	}
	return nil
}

// resolveRMDYDeps returns the staged dependency directory when configured or
// discovered. REMEDY_RMDY_DEPS set but missing fails closed.
func resolveRMDYDeps() (string, bool, error) {
	if override := strings.TrimSpace(os.Getenv(envRMDYDeps)); override != "" {
		abs, err := filepath.Abs(override)
		if err != nil {
			return "", false, err
		}
		st, err := os.Stat(abs)
		if err != nil || !st.IsDir() {
			return "", false, fmt.Errorf("%w: REMEDY_RMDY_DEPS missing or not a directory: %s", ErrWorkerAttachRequired, abs)
		}
		return abs, true, nil
	}
	candidates := []string{}
	// Packaging always stages the directory next to the zipapp.
	if pyz, ok, err := resolveRMDYPyz(); err == nil && ok {
		candidates = append(candidates, filepath.Join(filepath.Dir(pyz), depsDirName))
	}
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		candidates = append(candidates,
			filepath.Join(dir, depsDirName),
			filepath.Join(dir, "bin", depsDirName),
			filepath.Join(dir, "resources", depsDirName),
			filepath.Join(dir, "..", "resources", depsDirName),
		)
	}
	if res := strings.TrimSpace(os.Getenv("REMEDY_RESOURCES")); res != "" {
		candidates = append(candidates, filepath.Join(res, depsDirName))
	}
	if home := strings.TrimSpace(os.Getenv("REMEDY_HOME")); home != "" {
		candidates = append(candidates, filepath.Join(home, "bin", depsDirName))
	}
	if cwd, err := os.Getwd(); err == nil {
		candidates = append(candidates,
			filepath.Join(cwd, "dist", depsDirName),
			filepath.Join(cwd, "desktop", "bin", depsDirName),
		)
	}
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && st.IsDir() {
			abs, err := filepath.Abs(c)
			if err != nil {
				continue
			}
			return abs, true, nil
		}
	}
	return "", false, nil
}

func defaultToolEndpoint() (string, error) {
	var nonce [4]byte
	if _, err := rand.Read(nonce[:]); err != nil {
		return "", err
	}
	suffix := hex.EncodeToString(nonce[:])
	switch runtime.GOOS {
	case "windows":
		return `\\.\pipe\remedy-tools-` + suffix, nil
	default:
		dir := os.TempDir()
		if home := strings.TrimSpace(os.Getenv("REMEDY_HOME")); home != "" {
			dir = filepath.Join(home, "run")
			_ = os.MkdirAll(dir, 0o700)
		}
		return filepath.Join(dir, "remedy-tools-"+suffix+".sock"), nil
	}
}

func defaultPythonWorkerArgv() ([]string, error) {
	pyz, pyzOK, pyzErr := resolveRMDYPyz()
	if pyzErr != nil {
		return nil, pyzErr
	}
	workerArgv := func(python string) []string {
		if pyzOK {
			return []string{python, pyz}
		}
		return []string{python, "-m", "remedy.runtime.rmdy_tool_worker"}
	}

	if override := strings.TrimSpace(os.Getenv(envPython)); override != "" {
		abs, err := filepath.Abs(override)
		if err != nil {
			return nil, err
		}
		if st, err := os.Stat(abs); err != nil || st.IsDir() {
			return nil, fmt.Errorf("%w: REMEDY_PYTHON is not an absolute file: %s", ErrWorkerAttachRequired, abs)
		}
		return workerArgv(abs), nil
	}
	// Dev tree: the project .venv has pydantic/yaml/etc. installed, unlike a
	// stray PATH python — prefer it whenever we are running from a checkout,
	// including when a zipapp was built into dist/ or desktop/bin.
	if venv := repoVenvPython(); venv != "" {
		return workerArgv(venv), nil
	}
	for _, name := range []string{"python", "python3"} {
		if abs := lookPathAbs(name); abs != "" && !isWindowsStorePythonStub(abs) {
			return workerArgv(abs), nil
		}
	}
	// Packaged Desktop often has no PATH python; reuse or download the
	// managed CPython under ~/.remedy/voice/runtime (shared with voice).
	if abs, err := ensureManagedPython(); err == nil && abs != "" {
		return workerArgv(abs), nil
	} else if err != nil && pyzOK {
		return nil, fmt.Errorf("%w: no python interpreter found (%v); set REMEDY_PYTHON or allow managed download", ErrWorkerAttachRequired, err)
	}
	if uv := lookPathAbs("uv"); uv != "" {
		return []string{uv, "run", "python", "-m", "remedy.runtime.rmdy_tool_worker"}, nil
	}
	return nil, errors.New("no python interpreter found (set REMEDY_PYTHON to an absolute path)")
}

// PythonWorkerNeedsDownload reports whether resolving the worker interpreter
// would have to download the managed CPython (no REMEDY_PYTHON, no repo
// .venv, no PATH python, no managed runtime yet). Callers use it to bind the
// HTTP API first and attach the worker asynchronously.
func PythonWorkerNeedsDownload() bool {
	if strings.TrimSpace(os.Getenv(envPython)) != "" {
		return false
	}
	if _, _, err := resolveRMDYPyz(); err != nil {
		return false
	}
	if repoVenvPython() != "" {
		return false
	}
	for _, name := range []string{"python", "python3"} {
		if abs := lookPathAbs(name); abs != "" && !isWindowsStorePythonStub(abs) {
			return false
		}
	}
	if managedVoicePython() != "" {
		return false
	}
	return !skipManagedPythonDownload()
}

// repoVenvPython returns <repo>/.venv python when remedy-runtime runs from a
// source checkout (pyproject.toml above cwd or the executable).
func repoVenvPython() string {
	starts := []string{}
	if wd, err := os.Getwd(); err == nil {
		starts = append(starts, wd)
	}
	if exe, err := os.Executable(); err == nil {
		starts = append(starts, filepath.Dir(exe))
	}
	for _, start := range starts {
		repo := findRepoRoot(start)
		if repo == "" {
			continue
		}
		var cand string
		if runtime.GOOS == "windows" {
			cand = filepath.Join(repo, ".venv", "Scripts", "python.exe")
		} else {
			cand = filepath.Join(repo, ".venv", "bin", "python")
		}
		if st, err := os.Stat(cand); err == nil && !st.IsDir() {
			if abs, err := filepath.Abs(cand); err == nil {
				return abs
			}
		}
	}
	return ""
}

// managedVoicePython returns the owner's managed voice CPython when present
// (same tree voice.runtime.python_path uses). Empty when not installed.
func managedVoicePython() string {
	if override := strings.TrimSpace(os.Getenv("REMEDY_VOICE_PYTHON")); override != "" {
		if abs, err := filepath.Abs(override); err == nil {
			if st, err := os.Stat(abs); err == nil && !st.IsDir() {
				return abs
			}
		}
	}
	homes := make([]string, 0, 2)
	if h := strings.TrimSpace(os.Getenv("REMEDY_HOME")); h != "" {
		homes = append(homes, h)
	}
	if uh, err := os.UserHomeDir(); err == nil && uh != "" {
		homes = append(homes, filepath.Join(uh, ".remedy"))
	}
	for _, home := range homes {
		var cand string
		if runtime.GOOS == "windows" {
			cand = filepath.Join(home, "voice", "runtime", "python", "python.exe")
		} else {
			cand = filepath.Join(home, "voice", "runtime", "python", "bin", "python3")
		}
		if st, err := os.Stat(cand); err == nil && !st.IsDir() {
			if abs, err := filepath.Abs(cand); err == nil {
				return abs
			}
		}
	}
	return ""
}

// isWindowsStorePythonStub rejects WindowsApps alias stubs that are not a
// real interpreter (they open the Store / fail spawn).
//
// Normalize both separators so a Windows path string still matches when
// inspected on Linux (shared homes / CI).
func isWindowsStorePythonStub(path string) bool {
	p := strings.ToLower(filepath.ToSlash(path))
	p = strings.ReplaceAll(p, `\`, "/")
	return strings.Contains(p, "/windowsapps/")
}

// resolveRMDYPyz returns an absolute zipapp path when configured or discovered.
// REMEDY_RMDY_PYZ set but missing fails closed.
func resolveRMDYPyz() (string, bool, error) {
	if override := strings.TrimSpace(os.Getenv(envRMDYPyz)); override != "" {
		abs, err := filepath.Abs(override)
		if err != nil {
			return "", false, err
		}
		st, err := os.Stat(abs)
		if err != nil || st.IsDir() {
			return "", false, fmt.Errorf("%w: REMEDY_RMDY_PYZ missing or not a file: %s", ErrWorkerAttachRequired, abs)
		}
		return abs, true, nil
	}
	candidates := []string{}
	if exe, err := os.Executable(); err == nil {
		dir := filepath.Dir(exe)
		candidates = append(candidates,
			filepath.Join(dir, "rmdy_tool_worker.pyz"),
			filepath.Join(dir, "bin", "rmdy_tool_worker.pyz"),
			filepath.Join(dir, "resources", "rmdy_tool_worker.pyz"),
			filepath.Join(dir, "..", "resources", "rmdy_tool_worker.pyz"),
		)
	}
	if res := strings.TrimSpace(os.Getenv("REMEDY_RESOURCES")); res != "" {
		candidates = append(candidates, filepath.Join(res, "rmdy_tool_worker.pyz"))
	}
	if home := strings.TrimSpace(os.Getenv("REMEDY_HOME")); home != "" {
		candidates = append(candidates, filepath.Join(home, "bin", "rmdy_tool_worker.pyz"))
	}
	if cwd, err := os.Getwd(); err == nil {
		candidates = append(candidates, filepath.Join(cwd, "dist", "rmdy_tool_worker.pyz"))
		candidates = append(candidates, filepath.Join(cwd, "desktop", "bin", "rmdy_tool_worker.pyz"))
	}
	for _, c := range candidates {
		if st, err := os.Stat(c); err == nil && !st.IsDir() {
			abs, err := filepath.Abs(c)
			if err != nil {
				continue
			}
			return abs, true, nil
		}
	}
	return "", false, nil
}

func lookPathAbs(name string) string {
	if filepath.IsAbs(name) {
		if st, err := os.Stat(name); err == nil && !st.IsDir() {
			return name
		}
		return ""
	}
	exts := []string{""}
	if runtime.GOOS == "windows" {
		pathExt := os.Getenv("PATHEXT")
		if pathExt == "" {
			pathExt = ".COM;.EXE;.BAT;.CMD"
		}
		exts = strings.Split(strings.ToLower(pathExt), ";")
		for i := range exts {
			exts[i] = strings.TrimSpace(exts[i])
		}
	}
	pathEnv := os.Getenv("PATH")
	for _, dir := range filepath.SplitList(pathEnv) {
		if dir == "" {
			continue
		}
		candidates := []string{filepath.Join(dir, name)}
		if runtime.GOOS == "windows" {
			candidates = candidates[:0]
			lower := strings.ToLower(name)
			hasExt := false
			for _, ext := range exts {
				if ext != "" && strings.HasSuffix(lower, strings.ToLower(ext)) {
					hasExt = true
					break
				}
			}
			if hasExt {
				candidates = append(candidates, filepath.Join(dir, name))
			} else {
				for _, ext := range exts {
					candidates = append(candidates, filepath.Join(dir, name+ext))
				}
			}
		}
		for _, cand := range candidates {
			if st, err := os.Stat(cand); err == nil && !st.IsDir() {
				if abs, err := filepath.Abs(cand); err == nil {
					return abs
				}
				return cand
			}
		}
	}
	return ""
}

func inheritEnv() map[string]string {
	env := map[string]string{}
	for _, e := range os.Environ() {
		if i := strings.IndexByte(e, '='); i > 0 {
			env[e[:i]] = e[i+1:]
		}
	}
	return env
}

func enrichWorkerEnv(env map[string]string, cwd string) {
	if env == nil {
		return
	}
	start := strings.TrimSpace(cwd)
	if start == "" {
		if wd, err := os.Getwd(); err == nil {
			start = wd
		}
	}
	if strings.TrimSpace(env["REMEDY_WORKSPACE"]) == "" {
		if ws := resolveDefaultWorkspace(env, start); ws != "" {
			env["REMEDY_WORKSPACE"] = ws
		}
	}
	if strings.TrimSpace(env["PYTHONPATH"]) == "" {
		if src := findPythonSrc(start); src != "" {
			env["PYTHONPATH"] = src
		}
	}
}

// resolveDefaultWorkspace picks a workspace root that is never the packaged
// Desktop install folder (cwd when remedy-runtime is launched as a sidecar).
// Order: REMEDY_PROJECT_PATH → config.toml project_path → repo root (dev) →
// ~/Documents/Remedy (or ~/.remedy/workspace) — not the entire user home.
func resolveDefaultWorkspace(env map[string]string, start string) string {
	if p := strings.TrimSpace(env["REMEDY_PROJECT_PATH"]); p != "" && !isUserHomePath(p) && !looksLikeInstallDir(p) {
		return p
	}
	if p := strings.TrimSpace(env["REMEDY_PROJECT"]); p != "" && !isUserHomePath(p) && !looksLikeInstallDir(p) {
		return p
	}
	home := strings.TrimSpace(env["REMEDY_HOME"])
	if home == "" {
		home = strings.TrimSpace(os.Getenv("REMEDY_HOME"))
	}
	if home != "" {
		if p := projectPathFromConfig(home); p != "" && !looksLikeInstallDir(p) && !isUserHomePath(p) {
			return p
		}
	}
	if repo := findRepoRoot(start); repo != "" && !looksLikeInstallDir(repo) {
		return repo
	}
	if owner := defaultOwnerWorkspaceDir(); owner != "" {
		return owner
	}
	if start != "" && !looksLikeInstallDir(start) {
		return start
	}
	return ""
}

func isUserHomePath(path string) bool {
	p := filepath.Clean(strings.TrimSpace(path))
	if p == "" {
		return false
	}
	uh, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(uh) == "" {
		return false
	}
	return strings.EqualFold(p, filepath.Clean(uh))
}

// defaultOwnerWorkspaceDir is a narrow folder for agency tools when no
// session/config project is set — never the whole user profile.
func defaultOwnerWorkspaceDir() string {
	userHome, err := os.UserHomeDir()
	if err != nil || strings.TrimSpace(userHome) == "" {
		return ""
	}
	docs := filepath.Join(userHome, "Documents", "Remedy")
	if err := os.MkdirAll(docs, 0o755); err == nil {
		return docs
	}
	fallback := filepath.Join(userHome, ".remedy", "workspace")
	if err := os.MkdirAll(fallback, 0o755); err == nil {
		return fallback
	}
	return ""
}

func projectPathFromConfig(home string) string {
	raw, err := os.ReadFile(filepath.Join(home, "config.toml"))
	if err != nil {
		return ""
	}
	for _, line := range strings.Split(string(raw), "\n") {
		s := strings.TrimSpace(line)
		if s == "" || strings.HasPrefix(s, "#") {
			continue
		}
		if !strings.HasPrefix(strings.ToLower(s), "project_path") {
			continue
		}
		parts := strings.SplitN(s, "=", 2)
		if len(parts) != 2 {
			continue
		}
		val := strings.TrimSpace(parts[1])
		val = strings.Trim(val, `"'`)
		val = strings.TrimSpace(val)
		// Writers escape Windows paths as C:\\Users\\… in TOML strings.
		val = strings.ReplaceAll(val, `\\`, `\`)
		if val == "" || val == "." || val == "./" {
			return ""
		}
		return filepath.Clean(val)
	}
	return ""
}

func looksLikeInstallDir(path string) bool {
	p := filepath.Clean(strings.TrimSpace(path))
	if p == "" {
		return false
	}
	markers := []string{
		"Remedy Desktop.exe",
		"remedy-runtime.exe",
		"remedy-runtime",
		"uninstall.exe",
	}
	for _, name := range markers {
		if _, err := os.Stat(filepath.Join(p, name)); err == nil {
			return true
		}
	}
	// Packaged layout always ships webui/ next to the exe.
	webui := filepath.Join(p, "webui")
	windows := filepath.Join(p, "windows")
	if st, err := os.Stat(webui); err == nil && st.IsDir() {
		if st2, err2 := os.Stat(windows); err2 == nil && st2.IsDir() {
			return true
		}
	}
	return false
}

func findRepoRoot(start string) string {
	for d := start; d != "" && d != filepath.Dir(d); d = filepath.Dir(d) {
		if _, err := os.Stat(filepath.Join(d, "pyproject.toml")); err == nil {
			return d
		}
	}
	return ""
}

func findPythonSrc(start string) string {
	for d := start; d != "" && d != filepath.Dir(d); d = filepath.Dir(d) {
		src := filepath.Join(d, "src")
		if st, err := os.Stat(filepath.Join(src, "remedy")); err == nil && st.IsDir() {
			return src
		}
	}
	return ""
}
