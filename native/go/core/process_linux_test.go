//go:build linux

package core

import (
	"runtime"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/secret"
)

func TestLinuxProcessSpawnAuthorized(t *testing.T) {
	if runtime.GOOS != "linux" {
		t.Skip("linux only")
	}
	ResetForTest()
	t.Cleanup(ResetForTest)
	if FindLibraryPath() == "" {
		t.Skip("libremedy_core.so not built")
	}
	home := t.TempDir()
	key, err := secret.EnsureHostSigningKey(home)
	if err != nil {
		t.Fatal(err)
	}
	if err := EnsureSigningKey(key); err != nil {
		t.Fatal(err)
	}
	_ = WriteJailSetRoots(nil)
	argv := []string{"/bin/sleep", "30"}
	token, nowMS, err := IssueProcessSpawnToken(argv, false)
	if err != nil {
		t.Fatal(err)
	}
	pid, handle, err := ProcessSpawnAuthorized(argv, "", nil, token, "", "", false, nowMS)
	if err != nil {
		t.Fatalf("spawn: %v", err)
	}
	if pid == 0 || handle == 0 {
		t.Fatalf("pid=%d handle=%d", pid, handle)
	}
	if code, err := ProcessWait(handle, 50); err != nil {
		t.Fatal(err)
	} else if code != nil {
		t.Fatalf("unexpected early exit %v", *code)
	}
	if err := ProcessKillTree(pid); err != nil {
		t.Fatal(err)
	}
	deadline := time.Now().Add(2 * time.Second)
	for {
		code, err := ProcessWait(handle, 100)
		if err != nil {
			t.Fatal(err)
		}
		if code != nil {
			break
		}
		if time.Now().After(deadline) {
			t.Fatal("timeout waiting for kill")
		}
	}
	if err := ProcessClose(handle); err != nil {
		t.Fatal(err)
	}
}
