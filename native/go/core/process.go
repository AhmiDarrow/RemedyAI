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
