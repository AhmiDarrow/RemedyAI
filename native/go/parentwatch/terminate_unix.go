//go:build unix

package parentwatch

import (
	"os"

	"golang.org/x/sys/unix"
)

func terminateSelf(p *os.Process) error {
	return p.Signal(unix.SIGTERM)
}
