//go:build windows

package secret

import (
	"os"

	"golang.org/x/sys/windows"
)

// DisableEcho turns console echo off on f so a typed secret is not shown
// (what Python getpass did). ok is false when f is not a console; then
// nothing changed. restore puts the console back and is safe to call twice.
func DisableEcho(f *os.File) (restore func(), ok bool) {
	if f == nil {
		return func() {}, false
	}
	h := windows.Handle(f.Fd())
	var old uint32
	if err := windows.GetConsoleMode(h, &old); err != nil {
		return func() {}, false
	}
	quiet := old &^ windows.ENABLE_ECHO_INPUT
	quiet |= windows.ENABLE_PROCESSED_INPUT | windows.ENABLE_LINE_INPUT
	if err := windows.SetConsoleMode(h, quiet); err != nil {
		return func() {}, false
	}
	done := false
	return func() {
		if done {
			return
		}
		done = true
		_ = windows.SetConsoleMode(h, old)
	}, true
}
