package core

import (
	"encoding/json"
	"fmt"
)

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
	var envRaw []byte
	if env != nil {
		envRaw, err = json.Marshal(env)
		if err != nil {
			return out, err
		}
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
	var envRaw []byte
	if env != nil {
		envRaw, err = json.Marshal(env)
		if err != nil {
			return 0, 0, err
		}
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
