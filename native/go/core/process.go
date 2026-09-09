package core

import (
	"encoding/json"
	"fmt"
	"os"
)

// spawnEnvJSON encodes env exactly as the authorized spawn exports receive it:
// a plain object of string values, or the {"env": ..., "replace_env": true}
// wrapper when the parent block is to be replaced rather than merged. The
// operation hash (PolicyHashSpawn) and the spawn itself both go through this
// one encoding, so a token cannot be minted for one environment and spent on
// another. A nil env without replacement means "inherit the parent block" and
// yields no JSON at all.
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
	var exitCode uint32
	var timedOut uint8
	var stdoutPtr, stdoutLen uintptr
	var stderrPtr, stderrLen uintptr
	status, err := lib.call(
		"remedy_core_process_exec_capture_authorized",
		bytesPtr(argvRaw), uintptr(len(argvRaw)),
		bytesPtr(cwdRaw), uintptr(len(cwdRaw)),
		bytesPtr(envRaw), uintptr(len(envRaw)),
		bytesPtr(tokenRaw), uintptr(len(tokenRaw)),
		bytesPtr(subjectRaw), uintptr(len(subjectRaw)),
		bytesPtr(scopeRaw), uintptr(len(scopeRaw)),
		uintptr(confirmed),
		uintptr(nowMS),
		uintptr(timeoutMS),
		uint32Ptr(&exitCode),
		uint8Ptr(&timedOut),
		unsafePtrPtr(&stdoutPtr), sizePtr(&stdoutLen),
		unsafePtrPtr(&stderrPtr), sizePtr(&stderrLen),
	)
	if err != nil {
		return out, err
	}
	if err := lib.check("process_exec_capture_authorized", int32(status)); err != nil {
		return out, err
	}
	out.ExitCode = exitCode
	out.TimedOut = timedOut != 0
	out.Stdout = takeBytes(lib, stdoutPtr, stdoutLen)
	out.Stderr = takeBytes(lib, stderrPtr, stderrLen)
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
	var outPID uint32
	var outHandle uint64
	confirmed := uint8(0)
	if ownerConfirmed {
		confirmed = 1
	}
	status, err := lib.call(
		"remedy_core_process_spawn_authorized",
		bytesPtr(argvRaw), uintptr(len(argvRaw)),
		bytesPtr(cwdRaw), uintptr(len(cwdRaw)),
		bytesPtr(envRaw), uintptr(len(envRaw)),
		bytesPtr(tokenRaw), uintptr(len(tokenRaw)),
		bytesPtr(subjectRaw), uintptr(len(subjectRaw)),
		bytesPtr(scopeRaw), uintptr(len(scopeRaw)),
		uintptr(confirmed),
		uintptr(nowMS),
		uint32Ptr(&outPID),
		uint64Ptr(&outHandle),
	)
	if err != nil {
		return 0, 0, err
	}
	if err := lib.check("process_spawn_authorized", int32(status)); err != nil {
		return 0, 0, err
	}
	return outPID, outHandle, nil
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
	var pid uint32
	var handle, stdinW, stdoutR, stderrR uint64
	status, err := lib.call(
		"remedy_core_process_spawn_piped_authorized",
		bytesPtr(argvRaw), uintptr(len(argvRaw)),
		bytesPtr(cwdRaw), uintptr(len(cwdRaw)),
		bytesPtr(envRaw), uintptr(len(envRaw)),
		bytesPtr(tokenRaw), uintptr(len(tokenRaw)),
		bytesPtr(subjectRaw), uintptr(len(subjectRaw)),
		bytesPtr(scopeRaw), uintptr(len(scopeRaw)),
		uintptr(confirmed),
		uintptr(nowMS),
		uint32Ptr(&pid),
		uint64Ptr(&handle),
		uint64Ptr(&stdinW),
		uint64Ptr(&stdoutR),
		uint64Ptr(&stderrR),
	)
	if err != nil {
		return out, err
	}
	if err := lib.check("process_spawn_piped_authorized", int32(status)); err != nil {
		return out, err
	}
	out = PipedProcess{PID: pid, Handle: handle, StdinWrite: stdinW, StdoutRead: stdoutR, StderrRead: stderrR}
	return out, nil
}

// ProcessWait waits up to timeoutMS. nil means still running.
func ProcessWait(handle uint64, timeoutMS uint32) (*uint32, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var exited uint8
	var code uint32
	status, err := lib.call(
		"remedy_core_process_wait",
		uintptr(handle),
		uintptr(timeoutMS),
		uint8Ptr(&exited),
		uint32Ptr(&code),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("process_wait", int32(status)); err != nil {
		return nil, err
	}
	if exited == 0 {
		return nil, nil
	}
	c := code
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
