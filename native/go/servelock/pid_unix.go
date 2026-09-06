//go:build unix

package servelock

import "golang.org/x/sys/unix"

func pidAlive(pid int) bool {
	if pid <= 0 {
		return false
	}
	err := unix.Kill(pid, 0)
	return err == nil
}
