//go:build windows

package sysres

import (
	"unsafe"

	"golang.org/x/sys/windows"
)

// memoryStatusEx mirrors MEMORYSTATUSEX.
type memoryStatusEx struct {
	Length               uint32
	MemoryLoad           uint32
	TotalPhys            uint64
	AvailPhys            uint64
	TotalPageFile        uint64
	AvailPageFile        uint64
	TotalVirtual         uint64
	AvailVirtual         uint64
	AvailExtendedVirtual uint64
}

var procGlobalMemoryStatusEx = windows.NewLazySystemDLL("kernel32.dll").NewProc("GlobalMemoryStatusEx")

func readMemory() (total, avail uint64) {
	var st memoryStatusEx
	st.Length = uint32(unsafe.Sizeof(st))
	if err := procGlobalMemoryStatusEx.Find(); err != nil {
		return 0, 0
	}
	r, _, _ := procGlobalMemoryStatusEx.Call(uintptr(unsafe.Pointer(&st)))
	if r == 0 {
		return 0, 0
	}
	return st.TotalPhys, st.AvailPhys
}
