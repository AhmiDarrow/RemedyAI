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
// Windows: Zig host. Linux: Go cannot load remedy_core in-process yet and Zig
// process spawn is unsupported — returns a clear error (fail closed).
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
	if override := strings.TrimSpace(os.Getenv(envPython)); override != "" {
		abs, err := filepath.Abs(override)
		if err != nil {
			return nil, err
		}
		return []string{abs, "-m", "remedy.runtime.rmdy_tool_worker"}, nil
	}
	for _, name := range []string{"python", "python3"} {
		if abs := lookPathAbs(name); abs != "" {
			return []string{abs, "-m", "remedy.runtime.rmdy_tool_worker"}, nil
		}
	}
	if uv := lookPathAbs("uv"); uv != "" {
		return []string{uv, "run", "python", "-m", "remedy.runtime.rmdy_tool_worker"}, nil
	}
	return nil, errors.New("no python interpreter found (set REMEDY_PYTHON to an absolute path)")
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
	repo := findRepoRoot(start)
	workspace := repo
	if workspace == "" {
		workspace = start
	}
	if strings.TrimSpace(env["REMEDY_WORKSPACE"]) == "" && workspace != "" {
		env["REMEDY_WORKSPACE"] = workspace
	}
	if strings.TrimSpace(env["PYTHONPATH"]) == "" {
		if src := findPythonSrc(start); src != "" {
			env["PYTHONPATH"] = src
		}
	}
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
