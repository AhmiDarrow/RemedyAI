//go:build windows

package core

import (
	"fmt"
	"unsafe"

	"golang.org/x/sys/windows"
)

type nativeLib struct {
	dll *windows.DLL
}

func openNative(path string) (nativeLib, error) {
	dll, err := windows.LoadDLL(path)
	if err != nil {
		return nativeLib{}, err
	}
	return nativeLib{dll: dll}, nil
}

func (n nativeLib) close() error {
	if n.dll == nil {
		return nil
	}
	return n.dll.Release()
}

func (n nativeLib) proc(name string) (*windows.Proc, error) {
	if n.dll == nil {
		return nil, ErrUnavailable
	}
	return n.dll.FindProc(name)
}

func (l *Library) call(name string, args ...uintptr) (uintptr, error) {
	p, err := l.raw.proc(name)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", name, err)
	}
	r1, _, _ := p.Call(args...)
	return r1, nil
}

func (l *Library) abiVersion() (uint32, error) {
	r, err := l.call("remedy_core_abi_version")
	if err != nil {
		return 0, err
	}
	return uint32(r), nil
}

func (l *Library) lastOSError() uint32 {
	r, err := l.call("remedy_core_last_os_error")
	if err != nil {
		return 0
	}
	return uint32(r)
}

func (l *Library) free(ptr uintptr, length uintptr) {
	if ptr == 0 || length == 0 {
		return
	}
	_, _ = l.call("remedy_core_free", ptr, length)
}

func bytesPtr(b []byte) uintptr {
	if len(b) == 0 {
		return 0
	}
	return uintptr(unsafe.Pointer(&b[0]))
}

func uint8Ptr(p *uint8) uintptr {
	if p == nil {
		return 0
	}
	return uintptr(unsafe.Pointer(p))
}

func sizePtr(p *uintptr) uintptr {
	return uintptr(unsafe.Pointer(p))
}

func uint32Ptr(p *uint32) uintptr {
	return uintptr(unsafe.Pointer(p))
}

func uint64Ptr(p *uint64) uintptr {
	return uintptr(unsafe.Pointer(p))
}

func int32Ptr(p *int32) uintptr {
	return uintptr(unsafe.Pointer(p))
}

func takeBytes(l *Library, ptr uintptr, length uintptr) []byte {
	if ptr == 0 || length == 0 {
		return nil
	}
	defer l.free(ptr, length)
	out := make([]byte, length)
	copy(out, unsafe.Slice((*byte)(unsafe.Pointer(ptr)), length))
	return out
}
