//go:build windows

package core

import (
	"os"
	"os/exec"
	"testing"
	"time"
)

// TestProcessKillTreeWindows exercises the kill-tree export the shell timeout
// and Stop paths rely on. It spawns a real child with the OS (not the
// authorized path) so the test needs no capability token.
func TestProcessKillTreeWindows(t *testing.T) {
	if os.Getenv("REMEDY_NATIVE_CORE_LIB") == "" {
		if _, err := Open(); err != nil {
			t.Skipf("remedy_core unavailable: %v", err)
		}
	}
	if _, err := Open(); err != nil {
		t.Skipf("remedy_core unavailable: %v", err)
	}
	cmd := exec.Command("cmd.exe", "/c", "ping -n 30 127.0.0.1 >nul")
	if err := cmd.Start(); err != nil {
		t.Fatalf("start: %v", err)
	}
	pid := uint32(cmd.Process.Pid)
	defer func() { _ = cmd.Process.Kill() }()

	done := make(chan error, 1)
	go func() { done <- ProcessKillTree(pid) }()
	select {
	case err := <-done:
		if err != nil {
			t.Fatalf("kill tree: %v", err)
		}
	case <-time.After(10 * time.Second):
		t.Fatal("ProcessKillTree hung")
	}
	_ = cmd.Wait()
}
