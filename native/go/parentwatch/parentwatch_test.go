package parentwatch

import (
	"os"
	"os/exec"
	"runtime"
	"sync/atomic"
	"testing"
	"time"
)

func TestStartIfDesktopSidecarNoopsWithoutEnv(t *testing.T) {
	t.Setenv(desktopSidecarEnv, "")
	called := atomic.Bool{}
	orig := startWatch
	startWatch = func(pid int) error {
		called.Store(true)
		t.Errorf("watch started unexpectedly for pid %d", pid)
		return nil
	}
	t.Cleanup(func() { startWatch = orig })
	StartIfDesktopSidecar()
	time.Sleep(20 * time.Millisecond)
	if called.Load() {
		t.Fatal("StartIfDesktopSidecar started a watch without REMEDY_DESKTOP_SIDECAR=1")
	}
}

func TestStartIfDesktopSidecarNoopsOnThisOSWhenNotDarwin(t *testing.T) {
	if runtime.GOOS == "darwin" {
		t.Skip("darwin starts the watch; covered by TestWatchPIDFiresWhenProcessExits")
	}
	t.Setenv(desktopSidecarEnv, "1")
	called := atomic.Bool{}
	orig := startWatch
	startWatch = func(pid int) error {
		called.Store(true)
		return nil
	}
	t.Cleanup(func() { startWatch = orig })
	StartIfDesktopSidecar()
	time.Sleep(20 * time.Millisecond)
	if called.Load() {
		t.Fatal("StartIfDesktopSidecar must be a no-op off Darwin")
	}
}

func TestWatchPIDFiresWhenProcessExits(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("kqueue EVFILT_PROC NOTE_EXIT is Darwin-only")
	}
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	defer func() { _ = cmd.Process.Kill(); _ = cmd.Wait() }()

	done := make(chan struct{})
	errCh := make(chan error, 1)
	go func() {
		errCh <- WatchPID(cmd.Process.Pid, func() { close(done) })
	}()
	// Give kqueue time to register before killing the process.
	time.Sleep(50 * time.Millisecond)
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = cmd.Wait()
	select {
	case <-done:
	case <-time.After(3 * time.Second):
		t.Fatal("WatchPID did not fire after the watched process exited")
	}
	select {
	case err := <-errCh:
		if err != nil {
			t.Fatalf("WatchPID returned %v", err)
		}
	case <-time.After(time.Second):
		t.Fatal("WatchPID did not return after onExit")
	}
}

func TestWatchPIDAlreadyDead(t *testing.T) {
	if runtime.GOOS != "darwin" {
		t.Skip("kqueue EVFILT_PROC NOTE_EXIT is Darwin-only")
	}
	cmd := exec.Command("sleep", "30")
	if err := cmd.Start(); err != nil {
		t.Fatal(err)
	}
	pid := cmd.Process.Pid
	if err := cmd.Process.Kill(); err != nil {
		t.Fatal(err)
	}
	_ = cmd.Wait()

	called := atomic.Bool{}
	if err := WatchPID(pid, func() { called.Store(true) }); err != nil {
		t.Fatalf("WatchPID on a dead pid: %v", err)
	}
	if !called.Load() {
		t.Fatal("WatchPID must invoke onExit when the pid is already gone")
	}
}

func TestWatchPIDRejectsInitAndSelf(t *testing.T) {
	if runtime.GOOS != "darwin" {
		if err := WatchPID(1, nil); err == nil {
			t.Fatal("expected an error off Darwin")
		}
		return
	}
	if err := WatchPID(1, func() { t.Error("onExit for pid 1") }); err != ErrNoParent {
		t.Fatalf("pid 1: got %v, want ErrNoParent", err)
	}
	if err := WatchPID(os.Getpid(), func() { t.Error("onExit for self") }); err != ErrNoParent {
		t.Fatalf("self: got %v, want ErrNoParent", err)
	}
}
