//go:build linux || darwin || freebsd || netbsd || openbsd || dragonfly

package secret

import (
	"os"

	"golang.org/x/sys/unix"
)

// DisableEcho turns terminal echo off on f so a typed secret is not shown
// (what Python getpass did). ok is false when f is not a terminal; then
// nothing changed. restore puts the terminal back and is safe to call twice.
func DisableEcho(f *os.File) (restore func(), ok bool) {
	if f == nil {
		return func() {}, false
	}
	fd := int(f.Fd())
	old, err := unix.IoctlGetTermios(fd, ioctlGetTermios)
	if err != nil {
		return func() {}, false
	}
	quiet := *old
	quiet.Lflag &^= unix.ECHO
	quiet.Lflag |= unix.ICANON | unix.ISIG
	quiet.Iflag |= unix.ICRNL
	if err := unix.IoctlSetTermios(fd, ioctlSetTermios, &quiet); err != nil {
		return func() {}, false
	}
	done := false
	return func() {
		if done {
			return
		}
		done = true
		_ = unix.IoctlSetTermios(fd, ioctlSetTermios, old)
	}, true
}
