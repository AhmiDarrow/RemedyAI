// Package core loads the Zig remedy_core C ABI (ConPTY, capture, HostSession,
// Tailscale, policy tokens). Fail-closed: missing or wrong-ABI libraries
// return ErrUnavailable.
package core

import (
	"errors"
	"fmt"
	"runtime"
)

// ABIVersion matches native/zig/include/remedy_core.h REMEDY_CORE_ABI_VERSION.
const ABIVersion = 7

const (
	StatusOK              = 0
	StatusInvalidArgument = 1
	StatusAccessDenied    = 2
	StatusOperationFailed = 3
	StatusUnsupported     = 4
	StatusEnvDenied       = 5
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

// last_os_error values set by the macOS host when a TCC permission is missing.
const (
	OSErrAccessibility   uint32 = 1001
	OSErrScreenRecording uint32 = 1002
)

// PlatformUnsupported wraps ErrUnsupported with a host-specific message.
// Computer use on macOS is fail-closed until an Accessibility / Screen
// Recording host is implemented; callers should keep errors.Is on ErrUnsupported.
func PlatformUnsupported(feature string) error {
	if runtime.GOOS == "darwin" {
		if feature == "" {
			return fmt.Errorf("%w: not yet supported on macOS", ErrUnsupported)
		}
		return fmt.Errorf("%w: %s is not yet supported on macOS", ErrUnsupported, feature)
	}
	if feature == "" {
		return ErrUnsupported
	}
	return fmt.Errorf("%w: %s", ErrUnsupported, feature)
}

// HostError is a non-OK remedy_core_status (optional Win32 last-error).
type HostError struct {
	Function string
	Status   int32
	OSError  uint32
}

func (e *HostError) Error() string {
	detail := statusName(e.Status)
	if e.Status == StatusAccessDenied {
		switch e.OSError {
		case OSErrAccessibility:
			return e.Function + ": Accessibility permission is required. Open System Settings → Privacy & Security → Accessibility, enable Remedy Desktop, then retry"
		case OSErrScreenRecording:
			return e.Function + ": Screen Recording permission is required. Open System Settings → Privacy & Security → Screen Recording, enable Remedy Desktop, then retry"
		}
	}
	if e.OSError != 0 {
		label := "errno"
		if runtime.GOOS == "windows" {
			label = "Win32 error"
		}
		return fmt.Sprintf("%s: %s (%s %d)", e.Function, detail, label, e.OSError)
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
		if runtime.GOOS == "darwin" {
			return "not yet supported on macOS"
		}
		return "unsupported on this platform"
	case StatusEnvDenied:
		return "environment denied"
	default:
		return fmt.Sprintf("status %d", status)
	}
}
