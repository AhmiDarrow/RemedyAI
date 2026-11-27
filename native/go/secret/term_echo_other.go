//go:build !linux && !windows && !darwin && !freebsd && !netbsd && !openbsd

package secret

import "errors"

// DisableEcho is unsupported here; the caller reads the line as typed.
func DisableEcho(uintptr) (func(), error) { return nil, errors.New("echo control unsupported") }

// IsTerminal is unknown here.
func IsTerminal(uintptr) bool { return false }
