package workers

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"errors"
	"fmt"
	"net"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
	"github.com/AhmiDarrow/RemedyAI/native/go/ipc"
	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

const (
	envRMDYEndpoint = "REMEDY_RMDY_ENDPOINT"
	envPython       = "REMEDY_PYTHON"
	envRMDYPyz      = "REMEDY_RMDY_PYZ"
	defaultAttach   = 15 * time.Second
)

// ErrWorkerAttachRequired is returned when the RMDY tool worker cannot be
// supervised or attached. Serve must fail closed — never pretend tools exist.
var ErrWorkerAttachRequired = errors.New("RMDY tool worker attach required")

// StartedProcess is one supervised child.
type StartedProcess struct {
	PID     uint32
	Handle  uint64 // Zig process handle; 0 for non-Zig starters
	Cleanup func() error
}

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
}

// RMDYToolSession is a live FrameCaller backed by a supervised Python worker.
type RMDYToolSession struct {
	Client   *ipc.Client
	Endpoint string
	PID      uint32

	listener net.Listener
	proc     *StartedProcess
	cancel   context.CancelFunc
}

// Close tears down the IPC client, listener, and child process.
func (s *RMDYToolSession) Close() error {
	if s == nil {
		return nil
	}
	var joined error
	if s.cancel != nil {
		s.cancel()
	}
	if s.Client != nil {
		joined = errors.Join(joined, s.Client.Close())
		s.Client = nil
	}
	if s.listener != nil {
		joined = errors.Join(joined, s.listener.Close())
		s.listener = nil
	}
	if s.proc != nil && s.proc.Cleanup != nil {
		joined = errors.Join(joined, s.proc.Cleanup())
		s.proc = nil
	}
	return joined
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
		token, nowMS, err := core.IssueProcessSpawnToken(argv, false)
		if err != nil {
			return nil, err
		}
		pid, handle, err := core.ProcessSpawnAuthorized(argv, cwd, env, token, "", "", false, nowMS)
		if err != nil {
			return nil, err
		}
		return &StartedProcess{
			PID:    pid,
			Handle: handle,
			Cleanup: func() error {
				var joined error
				if pid != 0 {
					joined = errors.Join(joined, core.ProcessKillTree(pid))
				}
				joined = errors.Join(joined, core.ProcessClose(handle))
				return joined
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

	proc, err := starter(sessionCtx, argv, env, opts.Cwd)
	if err != nil {
		_ = session.Close()
		return nil, fmt.Errorf("%w: spawn worker: %v", ErrWorkerAttachRequired, err)
	}
	session.proc = proc
	if proc != nil {
		session.PID = proc.PID
	}

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
		_ = session.Close()
		return nil, fmt.Errorf("%w: worker did not dial %s within %s", ErrWorkerAttachRequired, endpoint, timeout)
	case res := <-ch:
		if res.err != nil {
			_ = session.Close()
			return nil, fmt.Errorf("%w: accept: %v", ErrWorkerAttachRequired, res.err)
		}
		conn = res.conn
	}

	session.Client = ipc.NewClient(conn)
	return session, nil
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

	if override := strings.TrimSpace(os.Getenv(envPython)); override != "" {
		abs, err := filepath.Abs(override)
		if err != nil {
			return nil, err
		}
		if st, err := os.Stat(abs); err != nil || st.IsDir() {
			return nil, fmt.Errorf("%w: REMEDY_PYTHON is not an absolute file: %s", ErrWorkerAttachRequired, abs)
		}
		if pyzOK {
			return []string{abs, pyz}, nil
		}
		return []string{abs, "-m", "remedy.runtime.rmdy_tool_worker"}, nil
	}
	for _, name := range []string{"python", "python3"} {
		if abs := lookPathAbs(name); abs != "" {
			if pyzOK {
				return []string{abs, pyz}, nil
			}
			return []string{abs, "-m", "remedy.runtime.rmdy_tool_worker"}, nil
		}
	}
	if pyzOK {
		return nil, fmt.Errorf("%w: REMEDY_RMDY_PYZ set but no python interpreter found (set REMEDY_PYTHON)", ErrWorkerAttachRequired)
	}
	if uv := lookPathAbs("uv"); uv != "" {
		return []string{uv, "run", "python", "-m", "remedy.runtime.rmdy_tool_worker"}, nil
	}
	return nil, errors.New("no python interpreter found (set REMEDY_PYTHON to an absolute path)")
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
		candidates = append(candidates, filepath.Join(filepath.Dir(exe), "rmdy_tool_worker.pyz"))
		candidates = append(candidates, filepath.Join(filepath.Dir(exe), "bin", "rmdy_tool_worker.pyz"))
	}
	if home := strings.TrimSpace(os.Getenv("REMEDY_HOME")); home != "" {
		candidates = append(candidates, filepath.Join(home, "bin", "rmdy_tool_worker.pyz"))
	}
	if cwd, err := os.Getwd(); err == nil {
		candidates = append(candidates, filepath.Join(cwd, "dist", "rmdy_tool_worker.pyz"))
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
// user home.
func resolveDefaultWorkspace(env map[string]string, start string) string {
	if p := strings.TrimSpace(env["REMEDY_PROJECT_PATH"]); p != "" {
		return p
	}
	if p := strings.TrimSpace(env["REMEDY_PROJECT"]); p != "" {
		return p
	}
	home := strings.TrimSpace(env["REMEDY_HOME"])
	if home == "" {
		home = strings.TrimSpace(os.Getenv("REMEDY_HOME"))
	}
	if home != "" {
		if p := projectPathFromConfig(home); p != "" && !looksLikeInstallDir(p) {
			return p
		}
	}
	if repo := findRepoRoot(start); repo != "" && !looksLikeInstallDir(repo) {
		return repo
	}
	if userHome, err := os.UserHomeDir(); err == nil && strings.TrimSpace(userHome) != "" {
		return userHome
	}
	if start != "" && !looksLikeInstallDir(start) {
		return start
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
		if val == "" || val == "." || val == "./" {
			return ""
		}
		return val
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
