//go:build windows

package parentwatch

import "os"

func ShutdownSignals() []os.Signal {
	return nil
}
