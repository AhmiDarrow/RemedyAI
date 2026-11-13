//go:build unix

package parentwatch

import (
	"os"

	"golang.org/x/sys/unix"
)

// ShutdownSignals are extra signals the runtime should wait on besides
// os.Interrupt. Linux pdeathsig and the Darwin parent-PID watch both send
// SIGTERM.
func ShutdownSignals() []os.Signal {
	return []os.Signal{unix.SIGTERM}
}
