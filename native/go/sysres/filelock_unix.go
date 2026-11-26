//go:build !windows

package sysres

import (
	"os"

	"golang.org/x/sys/unix"
)

// LockFile takes an exclusive advisory lock on f, blocking until it is free.
func LockFile(f *os.File) error {
	return unix.Flock(int(f.Fd()), unix.LOCK_EX)
}

// UnlockFile releases LockFile.
func UnlockFile(f *os.File) error {
	return unix.Flock(int(f.Fd()), unix.LOCK_UN)
}
