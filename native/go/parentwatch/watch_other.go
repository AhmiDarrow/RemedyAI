//go:build !darwin

package parentwatch

import "fmt"

// WatchPID is implemented with kqueue on Darwin only. Other hosts use
// PR_SET_PDEATHSIG (Linux) or a job object (Windows) in the parent.
func WatchPID(pid int, onExit func()) error {
	_ = pid
	_ = onExit
	return fmt.Errorf("%w: parent PID watch is implemented on macOS", ErrNoParent)
}
