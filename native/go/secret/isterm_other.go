//go:build !linux && !darwin && !freebsd && !netbsd && !openbsd && !dragonfly && !windows

package secret

// IsTerminal is unknown here.
func IsTerminal(uintptr) bool { return false }
