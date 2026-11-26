//go:build windows

package sysres

import (
	"os"

	"golang.org/x/sys/windows"
)

// LockFile takes an exclusive advisory lock on f, blocking until it is free.
func LockFile(f *os.File) error {
	var ol windows.Overlapped
	return windows.LockFileEx(
		windows.Handle(f.Fd()),
		windows.LOCKFILE_EXCLUSIVE_LOCK,
		0,
		1,
		0,
		&ol,
	)
}

// UnlockFile releases LockFile.
func UnlockFile(f *os.File) error {
	var ol windows.Overlapped
	return windows.UnlockFileEx(windows.Handle(f.Fd()), 0, 1, 0, &ol)
}
