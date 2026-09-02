package main

import (
	"runtime"
	"testing"
)

func TestCurrentProbeDeclaresVersionedReadiness(t *testing.T) {
	got := currentProbe()
	if got.Status != "ready" || got.Protocol != 1 || got.ToolABI != 1 {
		t.Fatalf("unexpected probe: %+v", got)
	}
	if got.OS != runtime.GOOS || got.Arch != runtime.GOARCH {
		t.Fatalf("probe platform mismatch: %+v", got)
	}
}
