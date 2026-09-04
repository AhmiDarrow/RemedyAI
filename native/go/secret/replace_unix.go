//go:build !windows

package secret

import "os"

func replaceFile(source, destination string) error {
	return os.Rename(source, destination)
}
