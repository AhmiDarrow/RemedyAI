//go:build !windows && !linux

package core

import (
	"fmt"
	"runtime"
	"unsafe"
)

// nativeLib is a placeholder: in-process Zig load is Windows/Linux for this slice.
// Other GOOS callers fail closed via Open().
type nativeLib struct{}

func openNative(path string) (nativeLib, error) {
	return nativeLib{}, fmt.Errorf(
		"%w: in-process remedy_core load not wired on %s yet (path=%s)",
		ErrUnavailable, runtime.GOOS, path,
	)
}

func (n nativeLib) close() error { return nil }

func (l *Library) call(name string, _ ...uintptr) (uintptr, error) {
	return 0, fmt.Errorf("%s: %w", name, ErrUnavailable)
}

func (l *Library) abiVersion() (uint32, error) {
	return 0, ErrUnavailable
}

func (l *Library) lastOSError() uint32 { return 0 }

func (l *Library) free(ptr uintptr, length uintptr) {}

func bytesPtr(b []byte) uintptr {
	if len(b) == 0 {
		return 0
	}
	return uintptr(unsafe.Pointer(&b[0]))
}

func uint16SlicePtr(v []uint16) uintptr {
	if len(v) == 0 {
		return 0
	}
	return uintptr(unsafe.Pointer(&v[0]))
}

func uint8Ptr(p *uint8) uintptr {
	if p == nil {
		return 0
	}
	return uintptr(unsafe.Pointer(p))
}

func sizePtr(p *uintptr) uintptr { return uintptr(unsafe.Pointer(p)) }

func uint32Ptr(p *uint32) uintptr { return uintptr(unsafe.Pointer(p)) }

func uint64Ptr(p *uint64) uintptr { return uintptr(unsafe.Pointer(p)) }

func int32Ptr(p *int32) uintptr { return uintptr(unsafe.Pointer(p)) }

func takeBytes(_ *Library, ptr uintptr, length uintptr) []byte {
	if ptr == 0 || length == 0 {
		return nil
	}
	out := make([]byte, length)
	copy(out, unsafe.Slice((*byte)(unsafe.Pointer(ptr)), length))
	return out
}
