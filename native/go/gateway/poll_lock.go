package gateway

import (
	"crypto/sha256"
	"encoding/hex"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strconv"
	"strings"
	"sync"
	"time"
)

// StaleLockSeconds: a lock whose heartbeat is older than this is reclaimable
// even when the recorded pid is still alive (a hung poller must not hold the
// bot forever).
const StaleLockSeconds = 90.0

// processHolders prevents dual in-process long-pollers (flock is re-entrant).
var processHolders sync.Map // path -> *PollLock

// pidAliveFn is indirect so tests can simulate a live-but-hung holder.
var pidAliveFn = pidAlive

// PollLock is a non-blocking exclusive lock for one messenger channel poller.
// The lock file is never unlinked after release: exclusivity comes from the
// OS file lock, and a stable inode keeps flock semantics sound across
// processes.
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
	return NewPollLockForToken(home, channel, "")
}

// NewPollLockForToken scopes the lock to one bot identity so two bots of the
// same kind (different tokens) may poll from the same home concurrently. The
// file name carries a short hash of the token, never the token itself.
func NewPollLockForToken(home, channel, token string) *PollLock {
	ch := normalizeID(channel)
	if ch == "" {
		ch = "telegram"
	}
	stem := ch
	if tag := tokenTag(token); tag != "" {
		stem = ch + "_" + tag
	}
	path := filepath.Join(resolveHome(home), "locks", stem+"_getupdates.lock")
	return &PollLock{Path: path, Channel: ch}
}

func tokenTag(token string) string {
	token = strings.TrimSpace(token)
	if token == "" {
		return ""
	}
	sum := sha256.Sum256([]byte(token))
	return hex.EncodeToString(sum[:4])
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
	if raw, err := os.ReadFile(l.Path); err == nil {
		if pid, ts, ok := parseLockPayload(string(raw)); ok && pid != os.Getpid() {
			foreignPID = pid
			alive := pidAliveFn(pid)
			staleHB := ts > 0 && time.Since(time.Unix(int64(ts), 0)).Seconds() > StaleLockSeconds
			if !alive || staleHB {
				// Dead holder, or a live holder that stopped heartbeating:
				// drop the stale file so a fresh inode can be locked. When
				// the holder still pins the file the flock below fails and
				// the caller retries later.
				_ = os.Remove(l.Path)
				log.Printf("%s: reclaiming poll lock (pid=%d alive=%v stale_heartbeat=%v)", l.Channel, pid, alive, staleHB)
			}
		}
	}

	fh, err := os.OpenFile(l.Path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		return false
	}
	if err := tryLockFile(fh); err != nil {
		_ = fh.Close()
		return false
	}
	l.fh = fh
	l.closed = false
	if err := l.writePayloadLocked(); err != nil {
		_ = unlockFile(fh)
		_ = fh.Close()
		l.fh = nil
		return false
	}
	l.Held = true
	l.Reclaimed = foreignPID > 0
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

// Release drops the exclusive lock. The file stays on disk (see PollLock).
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
