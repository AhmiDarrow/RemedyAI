//go:build !windows && !linux

package sysres

func readMemory() (total, avail uint64) { return 0, 0 }
