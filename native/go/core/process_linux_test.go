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
	token, nowMS, err := IssueProcessSpawnToken(argv, nil, false, false)
	if err != nil {
		t.Fatal(err)
	}
	pid, handle, err := ProcessSpawnAuthorized(argv, "", nil, false, token, "", "", false, nowMS)
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

func TestLinuxProcessPipedCancelRepeat(t *testing.T) {
	if FindLibraryPath() == "" {
		t.Skip("libremedy_core.so not built")
	}
	ResetForTest()
	t.Cleanup(ResetForTest)
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
	for i := 0; i < 20; i++ {
		token, nowMS, err := IssueProcessSpawnToken(argv, nil, false, false)
		if err != nil {
			t.Fatalf("iter %d token: %v", i, err)
		}
		proc, err := ProcessSpawnPipedAuthorized(argv, "", nil, false, token, "", "", false, nowMS)
		if err != nil {
			t.Fatalf("iter %d spawn: %v", i, err)
		}
		if proc.PID == 0 || proc.Handle == 0 {
			t.Fatalf("iter %d pid=%d handle=%d", i, proc.PID, proc.Handle)
		}
		stdin, stdout, stderr := proc.Files()
		_ = stdin.Close()
		exited := make(chan error, 1)
		go func(handle uint64) {
			for {
				code, err := ProcessWait(handle, 100)
				if err != nil {
					exited <- err
					return
				}
				if code != nil {
					exited <- nil
					return
				}
			}
		}(proc.Handle)
		time.Sleep(50 * time.Millisecond)
		if err := ProcessKillTree(proc.PID); err != nil {
			t.Fatalf("iter %d kill: %v", i, err)
		}
		select {
		case err := <-exited:
			if err != nil {
				t.Fatalf("iter %d wait after kill: %v", i, err)
			}
		case <-time.After(3 * time.Second):
			t.Fatalf("iter %d wait hung after kill", i)
		}
		_ = stdout.Close()
		_ = stderr.Close()
		if err := ProcessClose(proc.Handle); err != nil {
			t.Fatalf("iter %d close: %v", i, err)
		}
	}
}
