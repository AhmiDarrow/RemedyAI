package servelock

import (
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func TestAcquireReleaseRoundTrip(t *testing.T) {
	home := t.TempDir()
	l := New(home)
	ok, msg := l.TryAcquire()
	if !ok {
		t.Fatalf("acquire: %s", msg)
	}
	if _, err := os.Stat(l.Path); err != nil {
		t.Fatalf("lock file missing: %v", err)
	}
	raw, _ := os.ReadFile(l.Path)
	pid, _, parsed := parseLockPayload(string(raw))
	if !parsed || pid != os.Getpid() {
		t.Fatalf("payload = %q", raw)
	}
	l.Release()
	if _, err := os.Stat(l.Path); !os.IsNotExist(err) {
		t.Fatalf("lock file should be gone after release, err=%v", err)
	}
}

func TestSameProcessReacquireAllowed(t *testing.T) {
	home := t.TempDir()
	a := New(home)
	ok, msg := a.TryAcquire()
	if !ok {
		t.Fatalf("first: %s", msg)
	}
	defer a.Release()

	b := New(home)
	ok2, msg2 := b.TryAcquire()
	if !ok2 {
		t.Fatalf("same pid reacquire must succeed: %s", msg2)
	}
}

func TestSecondAcquireFailsWhileFirstHolds(t *testing.T) {
	home := t.TempDir()
	a := New(home)
	ok, msg := a.TryAcquire()
	if !ok {
		t.Fatalf("first: %s", msg)
	}
	defer a.Release()

	holderPID := os.Getpid()
	prevAlive := pidAliveFn
	prevPID := getPIDFn
	defer func() {
		pidAliveFn = prevAlive
		getPIDFn = prevPID
	}()
	// Simulate a second process: different PID, holder still alive.
	getPIDFn = func() int { return holderPID + 1 }
	pidAliveFn = func(pid int) bool { return pid == holderPID }

	b := New(home)
	ok2, msg2 := b.TryAcquire()
	if ok2 {
		t.Fatal("second acquire must refuse while first holds")
	}
	if !strings.Contains(strings.ToLower(msg2), "already running") {
		t.Fatalf("clear owner error missing: %q", msg2)
	}
	if !strings.Contains(msg2, "pid=") {
		t.Fatalf("expected pid in message: %q", msg2)
	}
}

func TestLivePIDNotStolen(t *testing.T) {
	home := t.TempDir()
	path := LockPath(home)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("4242 1.000\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	prevAlive := pidAliveFn
	defer func() { pidAliveFn = prevAlive }()
	pidAliveFn = func(pid int) bool { return pid == 4242 }

	ok, msg := New(home).TryAcquire()
	if ok {
		t.Fatal("must not steal live foreign PID")
	}
	if !strings.Contains(strings.ToLower(msg), "already running") {
		t.Fatalf("clear owner error missing: %q", msg)
	}
	if !strings.Contains(msg, "pid=4242") {
		t.Fatalf("expected pid=4242 in message: %q", msg)
	}
	// File must remain
	if _, err := os.Stat(path); err != nil {
		t.Fatalf("live lock must not be unlinked: %v", err)
	}
}

func TestStaleLockReclaimedWhenPIDDead(t *testing.T) {
	home := t.TempDir()
	path := LockPath(home)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte("99999999 1.000\n"), 0o600); err != nil {
		t.Fatal(err)
	}

	l := New(home)
	ok, msg := l.TryAcquire()
	if !ok {
		t.Fatalf("stale reclaim failed: %s", msg)
	}
	defer l.Release()
	raw, _ := os.ReadFile(path)
	pid, _, parsed := parseLockPayload(string(raw))
	if !parsed || pid != os.Getpid() {
		t.Fatalf("reclaimed payload = %q", raw)
	}
}

func TestHeartbeatRefreshesTimestamp(t *testing.T) {
	home := t.TempDir()
	l := New(home)
	ok, msg := l.TryAcquire()
	if !ok {
		t.Fatalf("acquire: %s", msg)
	}
	defer l.Release()

	raw1, _ := os.ReadFile(l.Path)
	_, ts1, _ := parseLockPayload(string(raw1))
	time.Sleep(20 * time.Millisecond)
	l.Heartbeat()
	raw2, _ := os.ReadFile(l.Path)
	_, ts2, _ := parseLockPayload(string(raw2))
	if ts2 <= ts1 {
		t.Fatalf("heartbeat did not advance timestamp: %v -> %v", ts1, ts2)
	}
}

func TestLockPathUsesHome(t *testing.T) {
	home := t.TempDir()
	p := LockPath(home)
	if !strings.HasSuffix(filepath.ToSlash(p), "/locks/remedy_serve.lock") {
		t.Fatalf("path = %q", p)
	}
	if !strings.HasPrefix(p, home) {
		t.Fatalf("path %q not under home %q", p, home)
	}
}
