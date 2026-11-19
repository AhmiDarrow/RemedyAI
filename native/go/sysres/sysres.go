// Package sysres reports how much of this machine is free right now, so
// background work can size itself to the PC instead of finding the limit by
// making it unresponsive.
package sysres

import "runtime"

// Snapshot is one reading of the machine. Zero byte counts mean the platform
// could not be read; callers must treat that as "unknown", not "empty".
type Snapshot struct {
	LogicalCPUs    int
	TotalBytes     uint64
	AvailableBytes uint64
}

// Known reports whether the memory figures were actually read.
func (s Snapshot) Known() bool { return s.TotalBytes > 0 }

// AvailableFraction is free RAM over total RAM, or 1 when unknown.
func (s Snapshot) AvailableFraction() float64 {
	if !s.Known() {
		return 1
	}
	return float64(s.AvailableBytes) / float64(s.TotalBytes)
}

// Read takes a snapshot. It never fails: an unreadable platform yields a
// snapshot with only the CPU count set.
func Read() Snapshot {
	total, avail := readMemory()
	return Snapshot{LogicalCPUs: runtime.NumCPU(), TotalBytes: total, AvailableBytes: avail}
}
