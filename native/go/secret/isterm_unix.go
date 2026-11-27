//go:build linux || darwin || freebsd || netbsd || openbsd || dragonfly

package secret

import "golang.org/x/sys/unix"

// IsTerminal reports whether fd is a terminal.
func IsTerminal(fd uintptr) bool {
	_, err := unix.IoctlGetTermios(int(fd), ioctlGetTermios)
	return err == nil
}
