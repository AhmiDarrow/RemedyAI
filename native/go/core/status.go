// Package core loads the Zig remedy_core C ABI (ConPTY, capture, HostSession,
// Tailscale, policy tokens). Fail-closed: missing or wrong-ABI libraries
// return ErrUnavailable.
package core

import (
	"errors"
	"fmt"
)

// ABIVersion matches native/zig/include/remedy_core.h REMEDY_CORE_ABI_VERSION.
const ABIVersion = 7

const (
	StatusOK              = 0
	StatusInvalidArgument = 1
	StatusAccessDenied    = 2
	StatusOperationFailed = 3
	StatusUnsupported     = 4
)

const (
	CapabilityTokenSize  = 169
	ProcessSpawnRight    = uint64(1) << 2
	OwnerCheckpointRight = uint64(1) << 5
	DefaultSpawnSubject  = "agent:remedy"
	DefaultSpawnScope    = "workspace:local"
	TokenLifetimeMS      = 60_000
	ConptyPipeStdin      = 0
	ConptyPipeStdout     = 1
)

var (
	ErrUnavailable = errors.New("remedy_core unavailable")
	ErrUnsupported = errors.New("remedy_core unsupported on this platform")
)

// HostError is a non-OK remedy_core_status (optional Win32 last-error).
type HostError struct {
	Function string
	Status   int32
	OSError  uint32
}

func (e *HostError) Error() string {
	detail := statusName(e.Status)
	if e.OSError != 0 {
		return fmt.Sprintf("%s: %s (Win32 error %d)", e.Function, detail, e.OSError)
	}
	return fmt.Sprintf("%s: %s", e.Function, detail)
}

func statusName(status int32) string {
	switch status {
	case StatusOK:
		return "ok"
	case StatusInvalidArgument:
		return "invalid argument"
	case StatusAccessDenied:
		return "access denied"
	case StatusOperationFailed:
		return "operation failed"
	case StatusUnsupported:
		return "unsupported on this platform"
	default:
		return fmt.Sprintf("status %d", status)
	}
}
