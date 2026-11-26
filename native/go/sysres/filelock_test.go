package sysres

import (
	"os"
	"path/filepath"
	"testing"
)

func TestLockFileRoundTrip(t *testing.T) {
	f, err := os.Create(filepath.Join(t.TempDir(), "gpu.lock"))
	if err != nil {
		t.Fatal(err)
	}
	defer f.Close()
	if err := LockFile(f); err != nil {
		t.Fatalf("lock: %v", err)
	}
	if err := UnlockFile(f); err != nil {
		t.Fatalf("unlock: %v", err)
	}
}
