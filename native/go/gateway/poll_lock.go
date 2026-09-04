package gateway

import (
	"fmt"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// StaleLockSeconds matches Python: heartbeat older than this may be reclaimed
// when the OS exclusive lock is free.
const StaleLockSeconds = 90.0

// processHolders prevents dual in-process long-pollers (flock is re-entrant).
var processHolders sync.Map // path -> *PollLock

// PollLock is a non-blocking exclusive lock for one messenger channel poller.
type PollLock struct {
	Path      string
	Channel   string
	Held      bool
	Reclaimed bool

	fh     *os.File
	mu     sync.Mutex
	key    string
	closed bool
}

// NewPollLock prepares a lock under ~/.remedy/locks/{channel}_getupdates.lock.
func NewPollLock(home, channel string) *PollLock {
	ch := normalizeID(channel)
	if ch == "" {
		ch = "telegram"
	}
	path := filepath.Join(resolveHome(home), "locks", ch+"_getupdates.lock")
	return &PollLock{Path: path, Channel: ch}
}

func (l *PollLock) resolveKey() string {
	if l.key != "" {
		return l.key
	}
	abs, err := filepath.Abs(l.Path)
	if err != nil {
		l.key = l.Path
		return l.key
	}
	l.key = abs
	return l.key
}

// TryAcquire returns true if this process owns the poller.
func (l *PollLock) TryAcquire() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	if l.Held {
		return true
	}
	key := l.resolveKey()
	if otherAny, ok := processHolders.Load(key); ok {
		other := otherAny.(*PollLock)
		if other != l && other.isHeldOpen() {
			return false
		}
		processHolders.CompareAndDelete(key, other)
	}

	if err := os.MkdirAll(filepath.Dir(l.Path), 0o700); err != nil {
		return false
	}

	foreignPID := 0
	foreignAlive := false
	if raw, err := os.ReadFile(l.Path); err == nil {
		if pid, ts, ok := parseLockPayload(string(raw)); ok && pid != os.Getpid() {
			foreignPID = pid
			foreignAlive = pidAlive(pid)
			staleHB := ts > 0 && time.Since(time.Unix(int64(ts), 0)).Seconds() > StaleLockSeconds
			if !foreignAlive {
				_ = staleHB
				_ = os.Remove(l.Path)
			}
		}
	}

	fh, err := os.OpenFile(l.Path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return false
	}
	if err := tryLockFile(fh); err != nil {
		_ = fh.Close()
		_ = foreignAlive
		_ = foreignPID
		return false
	}
	l.fh = fh
	if err := l.writePayloadLocked(); err != nil {
		_ = unlockFile(fh)
		_ = fh.Close()
		l.fh = nil
		return false
	}
	l.Held = true
	l.Reclaimed = foreignPID > 0 && foreignPID != os.Getpid()
	processHolders.Store(key, l)
	return true
}

func (l *PollLock) isHeldOpen() bool {
	l.mu.Lock()
	defer l.mu.Unlock()
	return l.Held && l.fh != nil && !l.closed
}

// Heartbeat refreshes the lock timestamp.
func (l *PollLock) Heartbeat() {
	l.mu.Lock()
	defer l.mu.Unlock()
	if !l.Held || l.fh == nil {
		return
	}
	_ = l.writePayloadLocked()
}

// Release drops the exclusive lock and removes our payload file when we own it.
func (l *PollLock) Release() {
	l.mu.Lock()
	defer l.mu.Unlock()
	if !l.Held && l.fh == nil {
		return
	}
	if l.fh != nil {
		_ = unlockFile(l.fh)
		_ = l.fh.Close()
		l.fh = nil
		l.closed = true
	}
	if raw, err := os.ReadFile(l.Path); err == nil {
		if strings.HasPrefix(strings.TrimSpace(string(raw)), strconv.Itoa(os.Getpid())) {
			_ = os.Remove(l.Path)
		}
	}
	l.Held = false
	l.Reclaimed = false
	key := l.resolveKey()
	if cur, ok := processHolders.Load(key); ok && cur == l {
		processHolders.Delete(key)
	}
}

func (l *PollLock) writePayloadLocked() error {
	if l.fh == nil {
		return fmt.Errorf("no lock file")
	}
	if _, err := l.fh.Seek(0, 0); err != nil {
		return err
	}
	if err := l.fh.Truncate(0); err != nil {
		return err
	}
	payload := fmt.Sprintf("%d %.0f\n", os.Getpid(), float64(time.Now().Unix()))
	_, err := l.fh.WriteString(payload)
	if err != nil {
		return err
	}
	return l.fh.Sync()
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
