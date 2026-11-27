//go:build darwin || freebsd || netbsd || openbsd

package secret

import "golang.org/x/sys/unix"

// DisableEcho turns terminal echo off on fd so a typed secret is not shown.
// The returned func restores the previous mode.
func DisableEcho(fd uintptr) (func(), error) {
	old, err := unix.IoctlGetTermios(int(fd), unix.TIOCGETA)
	if err != nil {
		return nil, err
	}
	quiet := *old
	quiet.Lflag &^= unix.ECHO
	quiet.Lflag |= unix.ICANON | unix.ISIG
	if err := unix.IoctlSetTermios(int(fd), unix.TIOCSETA, &quiet); err != nil {
		return nil, err
	}
	return func() { _ = unix.IoctlSetTermios(int(fd), unix.TIOCSETA, old) }, nil
}

// IsTerminal reports whether fd is a terminal.
func IsTerminal(fd uintptr) bool {
	_, err := unix.IoctlGetTermios(int(fd), unix.TIOCGETA)
	return err == nil
}
