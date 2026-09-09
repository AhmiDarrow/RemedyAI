package httpapi

import (
	"log"
	"os"
	"path/filepath"
	"sort"
	"strings"
	"time"
)

// Turn logs hold the full evidence for a turn — every tool result and every
// image — so they are the largest thing Remedy writes per session and they
// would otherwise grow without limit under the owner's home. Retention keeps
// recent turns (the ones a reader or an attach can still want) and drops the
// rest oldest-first.
const (
	turnLogKeepPerSession = 40
	turnLogMaxAge         = 30 * 24 * time.Hour
)

// pruneTurnLogs removes old turn logs for one session, keeping the newest
// keep entries and anything younger than maxAge. Errors are logged, never
// fatal: losing evidence is bad, failing a turn over housekeeping is worse.
func pruneTurnLogs(homeDir, sessionID string, keep int, maxAge time.Duration, now time.Time) {
	dir := turnsDir(homeDir, sessionID)
	if dir == "" {
		return
	}
	entries, err := os.ReadDir(dir)
	if err != nil {
		if !os.IsNotExist(err) {
			log.Printf("turn log prune: read %s: %v", dir, err)
		}
		return
	}
	type turnFile struct {
		id  string
		mod time.Time
	}
	logs := make([]turnFile, 0, len(entries))
	for _, entry := range entries {
		if entry.IsDir() || !strings.HasSuffix(entry.Name(), ".jsonl") {
			continue
		}
		info, err := entry.Info()
		if err != nil {
			continue
		}
		logs = append(logs, turnFile{
			id:  strings.TrimSuffix(entry.Name(), ".jsonl"),
			mod: info.ModTime(),
		})
	}
	// Newest first, so everything past the keep count is a deletion candidate.
	sort.Slice(logs, func(i, j int) bool { return logs[i].mod.After(logs[j].mod) })
	for i, entry := range logs {
		tooMany := keep > 0 && i >= keep
		tooOld := maxAge > 0 && now.Sub(entry.mod) > maxAge
		if !tooMany && !tooOld {
			continue
		}
		if err := os.Remove(filepath.Join(dir, entry.id+".jsonl")); err != nil && !os.IsNotExist(err) {
			log.Printf("turn log prune: %s: %v", entry.id, err)
			continue
		}
		// The image sidecar directory shares the request id.
		if err := os.RemoveAll(filepath.Join(dir, entry.id)); err != nil && !os.IsNotExist(err) {
			log.Printf("turn log prune: images %s: %v", entry.id, err)
		}
	}
}
