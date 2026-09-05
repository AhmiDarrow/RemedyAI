//go:build linux

package core

import (
	"fmt"
	"reflect"
	"unsafe"

	"github.com/ebitengine/purego"
)

type nativeLib struct {
	handle uintptr
}

func openNative(path string) (nativeLib, error) {
	handle, err := purego.Dlopen(path, purego.RTLD_NOW|purego.RTLD_LOCAL)
	if err != nil {
		return nativeLib{}, err
	}
	return nativeLib{handle: handle}, nil
}

func (n nativeLib) close() error {
	if n.handle == 0 {
		return nil
	}
	return purego.Dlclose(n.handle)
}

func (n nativeLib) sym(name string) (uintptr, error) {
	if n.handle == 0 {
		return 0, ErrUnavailable
	}
	return purego.Dlsym(n.handle, name)
}

func (l *Library) call(name string, args ...uintptr) (uintptr, error) {
	sym, err := l.raw.sym(name)
	if err != nil {
		return 0, fmt.Errorf("%s: %w", name, err)
	}
	r1, _, _ := purego.SyscallN(sym, args...)
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
	// Build a temporary slice header over the C buffer, copy into Go memory,
	// then free. Avoids go vet unsafeptr on uintptr→Pointer→Slice.
	var view []byte
	hdr := (*reflect.SliceHeader)(unsafe.Pointer(&view))
	hdr.Data = ptr
	hdr.Len = int(length)
	hdr.Cap = int(length)
	out := make([]byte, int(length))
	copy(out, view)
	return out
}
