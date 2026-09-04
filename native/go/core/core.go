package core

import (
	"fmt"
)

// EnsureLoaded opens remedy_core (cached). Alias for Open used by Connect.
func EnsureLoaded() error {
	_, err := Open()
	return err
}

// TailscaleStatusJSON returns the UTF-8 status object from Zig.
func TailscaleStatusJSON() ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr, length uintptr
	status, err := lib.call(
		"remedy_core_tailscale_status",
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return nil, err
	}
	if int32(status) != StatusOK {
		return nil, fmt.Errorf("%w: tailscale_status status=%d", ErrUnavailable, int32(status))
	}
	if ptr == 0 || length == 0 {
		return nil, fmt.Errorf("%w: empty tailscale_status", ErrUnavailable)
	}
	return takeBytes(lib, ptr, length), nil
}

// TailscaleLoginJSON returns the UTF-8 login action object from Zig.
func TailscaleLoginJSON() ([]byte, error) {
	lib, err := Open()
	if err != nil {
		return nil, err
	}
	var ptr, length uintptr
	status, err := lib.call(
		"remedy_core_tailscale_login",
		unsafePtrPtr(&ptr),
		sizePtr(&length),
	)
	if err != nil {
		return nil, err
	}
	if int32(status) != StatusOK {
		return nil, fmt.Errorf("%w: tailscale_login status=%d", ErrUnavailable, int32(status))
	}
	if ptr == 0 || length == 0 {
		return nil, fmt.Errorf("%w: empty tailscale_login", ErrUnavailable)
	}
	return takeBytes(lib, ptr, length), nil
}

// TailscaleLaunchMSI starts msiexec /i for an absolute .msi path (Windows).
func TailscaleLaunchMSI(msiPath string) (uint32, error) {
	lib, err := Open()
	if err != nil {
		return 0, err
	}
	if msiPath == "" {
		return 0, fmt.Errorf("%w: empty msi path", ErrUnavailable)
	}
	msi := []byte(msiPath)
	var pid uint32
	status, err := lib.call(
		"remedy_core_tailscale_launch_msi",
		bytesPtr(msi),
		uintptr(len(msi)),
		uint32Ptr(&pid),
	)
	if err != nil {
		return 0, err
	}
	switch int32(status) {
	case StatusOK:
		return pid, nil
	case StatusUnsupported:
		return 0, ErrUnsupported
	default:
		return 0, fmt.Errorf("%w: tailscale_launch_msi status=%d", ErrUnavailable, int32(status))
	}
}
