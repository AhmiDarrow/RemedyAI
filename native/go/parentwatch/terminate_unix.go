//go:build unix

package parentwatch

import (
	"os"
	"syscall"
)

func terminateSelf(p *os.Process) error {
	return p.Signal(syscall.SIGTERM)
}
