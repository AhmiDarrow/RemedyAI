//go:build windows

package sysres

import "golang.org/x/sys/windows"

// DiskSpace returns the free and total bytes of the volume holding path, or
// zeros when it cannot be read.
func DiskSpace(path string) (free, total uint64) {
	name, err := windows.UTF16PtrFromString(path)
	if err != nil {
		return 0, 0
	}
	var avail, all, totalFree uint64
	if windows.GetDiskFreeSpaceEx(name, &avail, &all, &totalFree) != nil {
		return 0, 0
	}
	return avail, all
}
