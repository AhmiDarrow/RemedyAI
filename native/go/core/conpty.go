package core

import (
	"encoding/json"
	"fmt"
)

// ConptyAvailable reports CreatePseudoConsole availability.
func ConptyAvailable() (bool, error) {
	lib, err := Open()
	if err != nil {
		return false, err
	}
	var flag uint8
	status, err := lib.call("remedy_core_conpty_available", uint8Ptr(&flag))
	if err != nil {
		return false, err
	}
	st := int32(status)
	if st == StatusUnsupported {
		return false, nil
	}
	if err := lib.check("conpty_available", st); err != nil {
		return false, err
	}
	return flag != 0, nil
}

// ConptySpawnAuthorized starts argv under ConPTY with a capability token.
// argv[0] must be absolute. cols/rows of 0 default inside Zig (120x40).
func ConptySpawnAuthorized(
	argv []string,
	cwd string,
	env map[string]string,
	cols, rows uint16,
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
		return 0, 0, fmt.Errorf("conpty_spawn_authorized: empty token")
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
		"remedy_core_conpty_spawn_authorized",
		bytesPtr(argvRaw), uintptr(len(argvRaw)),
		bytesPtr(cwdRaw), uintptr(len(cwdRaw)),
		bytesPtr(envRaw), uintptr(len(envRaw)),
		uintptr(cols), uintptr(rows),
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
	if err := lib.check("conpty_spawn_authorized", int32(status)); err != nil {
		return 0, 0, err
	}
	return outPID, outHandle, nil
}

// ConptyWrite writes bytes to the session stdin pipe.
func ConptyWrite(handle uint64, data []byte) (int, error) {
	lib, err := Open()
	if err != nil {
		return 0, err
	}
	var written uintptr
	status, err := lib.call(
		"remedy_core_conpty_write",
		uintptr(handle),
		bytesPtr(data), uintptr(len(data)),
		sizePtr(&written),
	)
	if err != nil {
		return 0, err
	}
	if err := lib.check("conpty_write", int32(status)); err != nil {
		return 0, err
	}
	return int(written), nil
}

// ConptyRead reads up to maxLen from the session stdout pipe.
func ConptyRead(handle uint64, maxLen int) ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	if maxLen <= 0 {
		return nil, nil
	}
	buf := make([]byte, maxLen)
	var got uintptr
	status, err := lib.call(
		"remedy_core_conpty_read",
		uintptr(handle),
		bytesPtr(buf), uintptr(len(buf)),
		sizePtr(&got),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("conpty_read", int32(status)); err != nil {
		return nil, err
	}
	if got == 0 {
		return nil, nil
	}
	return append([]byte(nil), buf[:got]...), nil
}

// ConptyPoll returns exit code when exited, or nil while still running.
func ConptyPoll(handle uint64) (*uint32, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var exited uint8
	var code uint32
	status, err := lib.call(
		"remedy_core_conpty_poll",
		uintptr(handle),
		uint8Ptr(&exited),
		uint32Ptr(&code),
	)
	if err != nil {
		return nil, err
	}
	if err := lib.check("conpty_poll", int32(status)); err != nil {
		return nil, err
	}
	if exited == 0 {
		return nil, nil
	}
	c := code
	return &c, nil
}

// ConptyKill terminates the ConPTY child.
func ConptyKill(handle uint64) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	status, err := lib.call("remedy_core_conpty_kill", uintptr(handle))
	if err != nil {
		return err
	}
	return lib.check("conpty_kill", int32(status))
}

// ConptyClosePipe closes stdin (0) or stdout (1).
func ConptyClosePipe(handle uint64, which uint32) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	status, err := lib.call("remedy_core_conpty_close_pipe", uintptr(handle), uintptr(which))
	if err != nil {
		return err
	}
	return lib.check("conpty_close_pipe", int32(status))
}

// ConptyClose releases the session handle.
func ConptyClose(handle uint64) error {
	lib, err := Open()
	if err != nil {
		return err
	}
	status, err := lib.call("remedy_core_conpty_close", uintptr(handle))
	if err != nil {
		return err
	}
	return lib.check("conpty_close", int32(status))
}
