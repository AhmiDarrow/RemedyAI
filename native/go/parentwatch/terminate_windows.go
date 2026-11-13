//go:build windows

package parentwatch

import "os"

func terminateSelf(p *os.Process) error {
	return p.Kill()
}
