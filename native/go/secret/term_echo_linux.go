//go:build linux

package secret

import "golang.org/x/sys/unix"

// DisableEcho turns terminal echo off on fd so a typed secret is not shown.
// The returned func restores the previous mode. It fails when fd is not a
// terminal; the caller then reads the line as typed (getpass parity).
func DisableEcho(fd uintptr) (func(), error) {
	old, err := unix.IoctlGetTermios(int(fd), unix.TCGETS)
	if err != nil {
		return nil, err
	}
	quiet := *old
	quiet.Lflag &^= unix.ECHO
	quiet.Lflag |= unix.ICANON | unix.ISIG
	if err := unix.IoctlSetTermios(int(fd), unix.TCSETS, &quiet); err != nil {
		return nil, err
	}
	return func() { _ = unix.IoctlSetTermios(int(fd), unix.TCSETS, old) }, nil
}

// IsTerminal reports whether fd is a terminal.
func IsTerminal(fd uintptr) bool {
	_, err := unix.IoctlGetTermios(int(fd), unix.TCGETS)
	return err == nil
}
