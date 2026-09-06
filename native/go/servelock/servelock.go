// Package servelock enforces a single remedy-runtime (serve) per REMEDY_HOME.
//
// Contract matches Python remedy.interfaces.instance_lock: O_EXCL create,
// PID + heartbeat timestamp in ~/.remedy/locks/remedy_serve.lock, reclaim only
// when the holder PID is dead (live PID is never stolen), fail-closed unless
// REMEDY_SERVE_LOCK_FAIL_OPEN is set.
package servelock

import (
	"context"
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

const (
	lockName = "remedy_serve.lock"
	// HeartbeatInterval refreshes the lock timestamp while serve is alive.
	HeartbeatInterval = 30 * time.Second
)

// Test hooks (same-process dual-acquire / foreign-PID cases).
var (
	pidAliveFn = pidAlive
	getPIDFn   = os.Getpid
)

// Lock is an exclusive serve lock for one REMEDY_HOME.
type Lock struct {
	Home string
	Path string

	mu   sync.Mutex
	fh   *os.File
	held bool
}

// New prepares a lock under home/locks/remedy_serve.lock (home may be empty → REMEDY_HOME / ~/.remedy).
func New(home string) *Lock {
	h := resolveHome(home)
	return &Lock{
		Home: h,
		Path: filepath.Join(h, "locks", lockName),
	}
}

// LockPath returns the lock file path for home (same resolution as New).
func LockPath(home string) string {
	return New(home).Path
}

func resolveHome(home string) string {
	home = strings.TrimSpace(home)
	if home != "" {
		return home
	}
	if env := strings.TrimSpace(os.Getenv("REMEDY_HOME")); env != "" {
		return env
	}
	userHome, err := os.UserHomeDir()
	if err != nil || userHome == "" {
		return ""
	}
	return filepath.Join(userHome, ".remedy")
}

func failOpen() bool {
	v := strings.TrimSpace(strings.ToLower(os.Getenv("REMEDY_SERVE_LOCK_FAIL_OPEN")))
	return v == "1" || v == "true" || v == "yes"
}

// TryAcquire acquires the exclusive serve lock. Returns (ok, message).
func (l *Lock) TryAcquire() (bool, string) {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.held && l.fh != nil {
		return true, "already held by this process"
	}
	if l.Path == "" || l.Home == "" {
		if failOpen() {
			return true, "lock dir unavailable; continuing (fail-open)"
		}
		return false, "lock dir unavailable (empty REMEDY_HOME); cannot start serve safely"
	}

	if err := os.MkdirAll(filepath.Dir(l.Path), 0o700); err != nil {
		if failOpen() {
			return true, "lock dir unavailable; continuing (fail-open)"
		}
		return false, fmt.Sprintf("lock dir unavailable (%v); cannot start serve safely", err)
	}

	if st, err := os.Stat(l.Path); err == nil && !st.IsDir() {
		oldPID, _, _ := parseLockPayload(readFileString(l.Path))
		// Live holder keeps the lock even if the heartbeat timestamp is old.
		stale := !(oldPID > 0 && pidAliveFn(oldPID))
		if stale {
			_ = os.Remove(l.Path)
		} else if oldPID == getPIDFn() {
			return true, "already held by this process"
		} else {
			return false, fmt.Sprintf(
				"Remedy is already running (API lock held by pid=%d). "+
					"Quit the other Remedy Desktop / serve first, or close the process using %s.",
				oldPID, l.Path,
			)
		}
	}

	fh, err := os.OpenFile(l.Path, os.O_CREATE|os.O_EXCL|os.O_RDWR, 0o600)
	if err != nil {
		if os.IsExist(err) {
			return false, "Remedy is already running (could not create serve lock). Quit the other instance first."
		}
		if failOpen() {
			return true, fmt.Sprintf("lock open failed; continuing (%v)", err)
		}
		return false, fmt.Sprintf("lock open failed (%v); cannot start serve safely", err)
	}
	if err := writePayload(fh, getPIDFn()); err != nil {
		_ = fh.Close()
		_ = os.Remove(l.Path)
		if failOpen() {
			return true, fmt.Sprintf("lock open failed; continuing (%v)", err)
		}
		return false, fmt.Sprintf("lock open failed (%v); cannot start serve safely", err)
	}
	l.fh = fh
	l.held = true
	return true, "acquired"
}

// Heartbeat refreshes the lock timestamp while serve is alive.
func (l *Lock) Heartbeat() {
	l.mu.Lock()
	defer l.mu.Unlock()
	if !l.held || l.fh == nil {
		return
	}
	_ = writePayload(l.fh, getPIDFn())
}

// RunHeartbeat calls Heartbeat on HeartbeatInterval until ctx is done.
func (l *Lock) RunHeartbeat(ctx context.Context) {
	ticker := time.NewTicker(HeartbeatInterval)
	defer ticker.Stop()
	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			l.Heartbeat()
		}
	}
}

// Release drops the serve lock and removes the file when this process owns it.
func (l *Lock) Release() {
	l.mu.Lock()
	defer l.mu.Unlock()
	fh, path := l.fh, l.Path
	l.fh = nil
	l.held = false
	if fh != nil {
		_ = fh.Close()
	}
	if path == "" {
		return
	}
	raw := readFileString(path)
	if raw == "" {
		return
	}
	pid, _, ok := parseLockPayload(raw)
	if ok && pid == getPIDFn() {
		_ = os.Remove(path)
		return
	}
	// Best-effort cleanup if we cannot parse but we had held the fh.
	if fh != nil {
		_ = os.Remove(path)
	}
}

func writePayload(fh *os.File, pid int) error {
	if _, err := fh.Seek(0, 0); err != nil {
		return err
	}
	if err := fh.Truncate(0); err != nil {
		return err
	}
	payload := fmt.Sprintf("%d %.3f\n", pid, float64(time.Now().UnixNano())/1e9)
	if _, err := fh.WriteString(payload); err != nil {
		return err
	}
	return fh.Sync()
}

func parseLockPayload(raw string) (pid int, ts float64, ok bool) {
	parts := strings.Fields(strings.TrimSpace(raw))
	if len(parts) == 0 {
		return 0, 0, false
	}
	p, err := strconv.Atoi(parts[0])
	if err != nil {
		return 0, 0, false
	}
	if len(parts) >= 2 {
		if v, err := strconv.ParseFloat(parts[1], 64); err == nil {
			ts = v
		}
	}
	return p, ts, true
}

func readFileString(path string) string {
	b, err := os.ReadFile(path)
	if err != nil {
		return ""
	}
	return string(b)
}
