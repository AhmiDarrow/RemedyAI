//go:build linux

package sysres

import "os"

func readMemory() (total, avail uint64) {
	raw, err := os.ReadFile("/proc/meminfo")
	if err != nil {
		return 0, 0
	}
	return parseMeminfo(string(raw))
}
