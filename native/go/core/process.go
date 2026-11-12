package core

import (
	"encoding/json"
	"fmt"
	"os"
	"runtime"
	"unsafe"
)

// spawnEnvJSON encodes env exactly as the authorized spawn exports receive it:
// a plain object of string values, or the {"env": ..., "replace_env": true}
// wrapper when the parent block is to be replaced rather than merged. The
// operation hash (PolicyHashSpawn) and the spawn itself both go through this
// one encoding, so a token cannot be minted for one environment and spent on
// another. A nil env without replacement means "inherit the parent block" and
// yields no JSON at all.

// Packed FFI argument and result blocks matching ProcessSpawnAuthIn /
// ProcessSpawnOut / ProcessPipedSpawnOut / ProcessExecCaptureAuthIn /
// ProcessExecCaptureOut in spawn_auth.zig. The two-pointer pack exports
// keep spawn calls inside a reliable SyscallN argument count; the expanded
// 16–21 argument forms can drop out-params when GC runs.
type processSpawnAuthIn struct {
	argvJSON, argvLen   uintptr
	cwd, cwdLen         uintptr
	envJSON, envLen     uintptr
	token, tokenLen     uintptr
	subject, subjectLen uintptr
	scope, scopeLen     uintptr
	ownerConfirmed      uint8
	_                   [7]byte
	nowMS               uint64
}

type processSpawnOut struct {
	PID    uint32
	_      uint32
	Handle uint64
}

type processPipedSpawnOut struct {
	PID                    uint32
	_                      uint32
	Handle, StdinWrite     uint64
	StdoutRead, StderrRead uint64
}

type processExecCaptureAuthIn struct {
	processSpawnAuthIn
	timeoutMS uint32
	_         [4]byte
}

type processExecCaptureOut struct {
	ExitCode  uint32
	TimedOut  uint8
	_         [3]byte
	Stdout    uintptr
	StdoutLen uintptr
	Stderr    uintptr
	StderrLen uintptr
}

type processConptySpawnAuthIn struct {
	processSpawnAuthIn
	cols, rows uint16
	_          [4]byte
}

func newSpawnAuthIn(
	argvRaw, cwdRaw, envRaw, tokenRaw, subjectRaw, scopeRaw []byte,
	confirmed uint8,
	nowMS uint64,
) processSpawnAuthIn {
	return processSpawnAuthIn{
		argvJSON: bytesPtr(argvRaw), argvLen: uintptr(len(argvRaw)),
		cwd: bytesPtr(cwdRaw), cwdLen: uintptr(len(cwdRaw)),
		envJSON: bytesPtr(envRaw), envLen: uintptr(len(envRaw)),
		token: bytesPtr(tokenRaw), tokenLen: uintptr(len(tokenRaw)),
		subject: bytesPtr(subjectRaw), subjectLen: uintptr(len(subjectRaw)),
		scope: bytesPtr(scopeRaw), scopeLen: uintptr(len(scopeRaw)),
		ownerConfirmed: confirmed,
		nowMS:          nowMS,
	}
}

func spawnEnvJSON(env map[string]string, replaceEnv bool) ([]byte, error) {
	if env == nil && !replaceEnv {
		return nil, nil
	}
	if env == nil {
		env = map[string]string{}
	}
	if replaceEnv {
		return json.Marshal(map[string]any{"env": env, "replace_env": true})
	}
	return json.Marshal(env)
}

// ExecCaptureResult is the outcome of an authorized one-shot spawn.
type ExecCaptureResult struct {
	ExitCode uint32
	TimedOut bool
	Stdout   []byte
	Stderr   []byte
}

// ExecCaptureAuthorized authorizes argv then runs a hidden one-shot capture
// (stdout/stderr, timeout/kill-tree on Windows). argv[0] must be absolute.
// timeoutMS 0 defaults to 60000 inside Zig. On timeout ExitCode is 1 and
// TimedOut is true (signal-cli Python parity).
func ExecCaptureAuthorized(
	argv []string,
	cwd string,
	env map[string]string,
	replaceEnv bool,
	token []byte,
	subject, scope string,
	ownerConfirmed bool,
	nowMS uint64,
	timeoutMS uint32,
) (ExecCaptureResult, error) {
	var out ExecCaptureResult
	lib, err := Open()
	if err != nil {
		return out, err
	}
	if len(token) == 0 {
		return out, fmt.Errorf("exec_capture_authorized: empty token")
	}
	if len(argv) == 0 {
		return out, fmt.Errorf("exec_capture_authorized: empty argv")
	}
	argvRaw, err := json.Marshal(argv)
	if err != nil {
		return out, err
	}
	var cwdRaw []byte
	if cwd != "" {
		cwdRaw = []byte(cwd)
	}
	envRaw, err := spawnEnvJSON(env, replaceEnv)
	if err != nil {
		return out, err
	}
	if subject == "" {
		subject = DefaultSpawnSubject
	}
	if scope == "" {
		scope = DefaultSpawnScope
	}
	subjectRaw := []byte(subject)
	scopeRaw := []byte(scope)
	tokenRaw := append([]byte(nil), token...)
	confirmed := uint8(0)
	if ownerConfirmed {
		confirmed = 1
	}
	args := new(processExecCaptureAuthIn)
	args.processSpawnAuthIn = newSpawnAuthIn(argvRaw, cwdRaw, envRaw, tokenRaw, subjectRaw, scopeRaw, confirmed, nowMS)
	args.timeoutMS = timeoutMS
	captured := new(processExecCaptureOut)
	status, err := lib.call(
		"remedy_core_process_exec_capture_authorized_pack",
		uintptr(unsafe.Pointer(args)),
		uintptr(unsafe.Pointer(captured)),
	)
	runtime.KeepAlive(argvRaw)
	runtime.KeepAlive(cwdRaw)
	runtime.KeepAlive(envRaw)
	runtime.KeepAlive(tokenRaw)
	runtime.KeepAlive(subjectRaw)
	runtime.KeepAlive(scopeRaw)
	runtime.KeepAlive(args)
	runtime.KeepAlive(captured)
	if err != nil {
		return out, err
	}
	if err := lib.check("process_exec_capture_authorized", int32(status)); err != nil {
		return out, err
	}
	out.ExitCode = captured.ExitCode
	out.TimedOut = captured.TimedOut != 0
	out.Stdout = takeBytes(lib, captured.Stdout, captured.StdoutLen)
	out.Stderr = takeBytes(lib, captured.Stderr, captured.StderrLen)
	return out, nil
}

// ProcessSpawnAuthorized policy-checks then hidden-spawns argv (Zig).
// argv[0] must be absolute. No stdio pipes are attached — use IPC for framing.
// Windows and Linux (process group + kill-tree); other GOOS fail closed.
func ProcessSpawnAuthorized(
	argv []string,
	cwd string,
	env map[string]string,
	replaceEnv bool,
	token []byte,
	subject, scope string,
	ownerConfirmed bool,
	nowMS uint64,
) (pid uint32, handle uint64, err error) {
	lib, err := Open()
	if err != nil {
		return 0, 0, err
	}
	if len(token) == 0 {
		return 0, 0, fmt.Errorf("process_spawn_authorized: empty token")
	}
	argvRaw, err := json.Marshal(argv)
	if err != nil {
		return 0, 0, err
	}
	var cwdRaw []byte
	if cwd != "" {
		cwdRaw = []byte(cwd)
	}
	envRaw, err := spawnEnvJSON(env, replaceEnv)
	if err != nil {
		return 0, 0, err
	}
	if subject == "" {
		subject = DefaultSpawnSubject
	}
	if scope == "" {
		scope = DefaultSpawnScope
	}
	subjectRaw := []byte(subject)
	scopeRaw := []byte(scope)
	tokenRaw := append([]byte(nil), token...)
	confirmed := uint8(0)
	if ownerConfirmed {
		confirmed = 1
	}
	args := new(processSpawnAuthIn)
	*args = newSpawnAuthIn(argvRaw, cwdRaw, envRaw, tokenRaw, subjectRaw, scopeRaw, confirmed, nowMS)
	out := new(processSpawnOut)
	status, err := lib.call(
		"remedy_core_process_spawn_authorized_pack",
		uintptr(unsafe.Pointer(args)),
		uintptr(unsafe.Pointer(out)),
	)
	runtime.KeepAlive(argvRaw)
	runtime.KeepAlive(cwdRaw)
	runtime.KeepAlive(envRaw)
	runtime.KeepAlive(tokenRaw)
	runtime.KeepAlive(subjectRaw)
	runtime.KeepAlive(scopeRaw)
	runtime.KeepAlive(args)
	runtime.KeepAlive(out)
	if err != nil {
		return 0, 0, err
	}
	if err := lib.check("process_spawn_authorized", int32(status)); err != nil {
		return 0, 0, err
	}
	if out.PID == 0 || out.Handle == 0 {
		return 0, 0, fmt.Errorf("process_spawn_authorized: host returned an empty process handle")
	}
	return out.PID, out.Handle, nil
}

// PipedProcess is an authorized 3-pipe spawn. The pipe ends are parent-owned
// OS handles (Windows HANDLE / POSIX fd); Close them with the *os.File
// returned by Files. ProcessClose(Handle) does not close the pipe ends.
type PipedProcess struct {
	PID        uint32
	Handle     uint64
	StdinWrite uint64
	StdoutRead uint64
	StderrRead uint64
}

// Files wraps the parent pipe ends as *os.File. Call once; the caller owns
// closing them.
func (p PipedProcess) Files() (stdin, stdout, stderr *os.File) {
	return os.NewFile(uintptr(p.StdinWrite), "remedy-child-stdin"),
		os.NewFile(uintptr(p.StdoutRead), "remedy-child-stdout"),
		os.NewFile(uintptr(p.StderrRead), "remedy-child-stderr")
}

// ProcessSpawnPipedAuthorized policy-checks then hidden-spawns argv with
// separate stdin/stdout/stderr pipes (Zig; same auth and write-jail contract
// as ProcessSpawnAuthorized). argv[0] must be absolute. The returned Handle is
// waited / killed / released with ProcessWait, ProcessKillTree, ProcessClose.
func ProcessSpawnPipedAuthorized(
	argv []string,
	cwd string,
	env map[string]string,
	replaceEnv bool,
	token []byte,
	subject, scope string,
	ownerConfirmed bool,
	nowMS uint64,
) (PipedProcess, error) {
	var out PipedProcess
	lib, err := Open()
	if err != nil {
		return out, err
	}
	if len(token) == 0 {
		return out, fmt.Errorf("process_spawn_piped_authorized: empty token")
	}
	if len(argv) == 0 {
		return out, fmt.Errorf("process_spawn_piped_authorized: empty argv")
	}
	argvRaw, err := json.Marshal(argv)
	if err != nil {
		return out, err
	}
	var cwdRaw []byte
	if cwd != "" {
		cwdRaw = []byte(cwd)
	}
	envRaw, err := spawnEnvJSON(env, replaceEnv)
	if err != nil {
		return out, err
	}
	if subject == "" {
		subject = DefaultSpawnSubject
	}
	if scope == "" {
		scope = DefaultSpawnScope
	}
	subjectRaw := []byte(subject)
	scopeRaw := []byte(scope)
	tokenRaw := append([]byte(nil), token...)
	confirmed := uint8(0)
	if ownerConfirmed {
		confirmed = 1
	}
	args := new(processSpawnAuthIn)
	*args = newSpawnAuthIn(argvRaw, cwdRaw, envRaw, tokenRaw, subjectRaw, scopeRaw, confirmed, nowMS)
	spawned := new(processPipedSpawnOut)
	status, err := lib.call(
		"remedy_core_process_spawn_piped_authorized_pack",
		uintptr(unsafe.Pointer(args)),
		uintptr(unsafe.Pointer(spawned)),
	)
	runtime.KeepAlive(argvRaw)
	runtime.KeepAlive(cwdRaw)
	runtime.KeepAlive(envRaw)
	runtime.KeepAlive(tokenRaw)
	runtime.KeepAlive(subjectRaw)
	runtime.KeepAlive(scopeRaw)
	runtime.KeepAlive(args)
	runtime.KeepAlive(spawned)
	if err != nil {
		return out, err
	}
	if err := lib.check("process_spawn_piped_authorized", int32(status)); err != nil {
		return out, err
	}
	out = PipedProcess{
		PID: spawned.PID, Handle: spawned.Handle,
		StdinWrite: spawned.StdinWrite, StdoutRead: spawned.StdoutRead, StderrRead: spawned.StderrRead,
	}
	if out.PID == 0 || out.Handle == 0 {
		return PipedProcess{}, fmt.Errorf("process_spawn_piped_authorized: host returned an empty process handle")
	}
	return out, nil
}

// ProcessWait waits up to timeoutMS. nil means still running.
func ProcessWait(handle uint64, timeoutMS uint32) (*uint32, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	// Heap-allocate out params and KeepAlive them. uintptr addresses of
	// stack slots become dangling if the stack grows inside lib.call;
	// Zig then sees a nil out pointer and returns invalid argument.
	exited := new(uint8)
	code := new(uint32)
	status, err := lib.call(
		"remedy_core_process_wait",
		uintptr(handle),
		uintptr(timeoutMS),
		uint8Ptr(exited),
		uint32Ptr(code),
	)
	runtime.KeepAlive(exited)
	runtime.KeepAlive(code)
	if err != nil {
		return nil, err
	}
	if err := lib.check("process_wait", int32(status)); err != nil {
		return nil, err
	}
	if *exited == 0 {
		return nil, nil
	}
	c := *code
	return &c, nil
}

// ProcessKillTree terminates pid and descendants through remedy_core.
func ProcessKillTree(pid uint32) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	status, err := lib.call("remedy_core_process_kill_tree", uintptr(pid))
	if err != nil {
		return err
	}
	return lib.check("process_kill_tree", int32(status))
}

// ProcessClose releases the spawn handle (job close kills remaining children).
func ProcessClose(handle uint64) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	status, err := lib.call("remedy_core_process_close", uintptr(handle))
	if err != nil {
		return err
	}
	return lib.check("process_close", int32(status))
}
