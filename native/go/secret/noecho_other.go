//go:build !linux && !darwin && !freebsd && !netbsd && !openbsd && !dragonfly && !windows

package secret

import "os"

// DisableEcho is unavailable on this platform: ok is always false.
func DisableEcho(_ *os.File) (restore func(), ok bool) { return func() {}, false }
