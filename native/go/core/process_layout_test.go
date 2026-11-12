package core

import (
	"testing"
	"unsafe"
)

func TestProcessSpawnPackLayout(t *testing.T) {
	if n := unsafe.Sizeof(processSpawnAuthIn{}); n != 112 {
		t.Fatalf("processSpawnAuthIn size=%d want 112", n)
	}
	if n := unsafe.Sizeof(processSpawnOut{}); n != 16 {
		t.Fatalf("processSpawnOut size=%d want 16", n)
	}
	if n := unsafe.Sizeof(processPipedSpawnOut{}); n != 40 {
		t.Fatalf("processPipedSpawnOut size=%d want 40", n)
	}
}
