//go:build !windows

package sysres

import "path/filepath"

// RealPath returns where an existing path really is, with symlinks resolved.
func RealPath(p string) (string, error) {
	return filepath.EvalSymlinks(p)
}
