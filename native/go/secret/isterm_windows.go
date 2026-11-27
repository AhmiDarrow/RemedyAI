//go:build windows

package secret

import "golang.org/x/sys/windows"

// IsTerminal reports whether fd is a console.
func IsTerminal(fd uintptr) bool {
	var mode uint32
	return windows.GetConsoleMode(windows.Handle(fd), &mode) == nil
}
