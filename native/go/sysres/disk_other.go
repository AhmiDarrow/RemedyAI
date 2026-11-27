//go:build !windows

package sysres

import "golang.org/x/sys/unix"

// DiskSpace returns the free and total bytes of the volume holding path, or
// zeros when it cannot be read.
func DiskSpace(path string) (free, total uint64) {
	var st unix.Statfs_t
	if unix.Statfs(path, &st) != nil {
		return 0, 0
	}
	return uint64(st.Bavail) * uint64(st.Bsize), uint64(st.Blocks) * uint64(st.Bsize)
}
