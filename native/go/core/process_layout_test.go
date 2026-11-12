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
	if n := unsafe.Sizeof(processExecCaptureAuthIn{}); n != 120 {
		t.Fatalf("processExecCaptureAuthIn size=%d want 120", n)
	}
	if n := unsafe.Offsetof(processExecCaptureAuthIn{}.timeoutMS); n != 112 {
		t.Fatalf("processExecCaptureAuthIn.timeoutMS offset=%d want 112", n)
	}
	if n := unsafe.Sizeof(processExecCaptureOut{}); n != 40 {
		t.Fatalf("processExecCaptureOut size=%d want 40", n)
	}
	if n := unsafe.Offsetof(processExecCaptureOut{}.Stdout); n != 8 {
		t.Fatalf("processExecCaptureOut.Stdout offset=%d want 8", n)
	}
	if n := unsafe.Sizeof(processConptySpawnAuthIn{}); n != 120 {
		t.Fatalf("processConptySpawnAuthIn size=%d want 120", n)
	}
	if n := unsafe.Offsetof(processConptySpawnAuthIn{}.cols); n != 112 {
		t.Fatalf("processConptySpawnAuthIn.cols offset=%d want 112", n)
	}
}
