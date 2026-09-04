package core

import "unsafe"

// stringToCBytes returns a pointer to s's bytes for C ABI calls (s must stay live).
func stringToCBytes(s string) uintptr {
	if s == "" {
		return 0
	}
	return uintptr(unsafe.Pointer(unsafe.StringData(s)))
}
