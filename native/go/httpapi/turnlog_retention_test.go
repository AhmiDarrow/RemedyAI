package httpapi

import (
	"os"
	"path/filepath"
	"testing"
	"time"
)

func writeTurnLogFixture(t *testing.T, dir, id string, mod time.Time) {
	t.Helper()
	if err := os.MkdirAll(dir, 0o755); err != nil {
		t.Fatal(err)
	}
	logPath := filepath.Join(dir, id+".jsonl")
	if err := os.WriteFile(logPath, []byte(`{"t":"start"}`+"\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	// Every turn may also carry an image sidecar directory named by request id.
	imgDir := filepath.Join(dir, id)
	if err := os.MkdirAll(imgDir, 0o755); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(filepath.Join(imgDir, "a.png"), []byte("png"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.Chtimes(logPath, mod, mod); err != nil {
		t.Fatal(err)
	}
}

func TestPruneTurnLogsKeepsRecentAndDropsOld(t *testing.T) {
	home := t.TempDir()
	sid := "sess-prune"
	dir := turnsDir(home, sid)
	if dir == "" {
		t.Fatal("turnsDir empty for a configured home")
	}
	now := time.Now()
	// Newest first when sorted; ids encode their age for readability.
	for i := 0; i < 6; i++ {
		writeTurnLogFixture(t, dir, "turn-recent-"+string(rune('a'+i)), now.Add(-time.Duration(i)*time.Minute))
	}
	writeTurnLogFixture(t, dir, "turn-ancient", now.Add(-90*24*time.Hour))

	pruneTurnLogs(home, sid, 3, 30*24*time.Hour, now)

	entries, err := os.ReadDir(dir)
	if err != nil {
		t.Fatal(err)
	}
	kept := map[string]bool{}
	for _, e := range entries {
		kept[e.Name()] = true
	}
	// The three newest survive with their image directories.
	for _, id := range []string{"turn-recent-a", "turn-recent-b", "turn-recent-c"} {
		if !kept[id+".jsonl"] {
			t.Fatalf("%s should be kept: %v", id, kept)
		}
		if !kept[id] {
			t.Fatalf("%s image dir should be kept: %v", id, kept)
		}
	}
	// Everything past the keep count goes, and so do its images.
	for _, id := range []string{"turn-recent-d", "turn-recent-e", "turn-recent-f", "turn-ancient"} {
		if kept[id+".jsonl"] {
			t.Fatalf("%s should be pruned: %v", id, kept)
		}
		if kept[id] {
			t.Fatalf("%s image dir should be pruned: %v", id, kept)
		}
	}
}

func TestPruneTurnLogsDropsAgedOutEvenUnderTheKeepCount(t *testing.T) {
	home := t.TempDir()
	sid := "sess-age"
	dir := turnsDir(home, sid)
	now := time.Now()
	writeTurnLogFixture(t, dir, "fresh", now)
	writeTurnLogFixture(t, dir, "stale", now.Add(-60*24*time.Hour))

	pruneTurnLogs(home, sid, 100, 30*24*time.Hour, now)

	if _, err := os.Stat(filepath.Join(dir, "fresh.jsonl")); err != nil {
		t.Fatalf("fresh turn must survive: %v", err)
	}
	if _, err := os.Stat(filepath.Join(dir, "stale.jsonl")); !os.IsNotExist(err) {
		t.Fatalf("stale turn must be pruned, stat err=%v", err)
	}
}

func TestPruneTurnLogsWithoutHomeIsANoop(t *testing.T) {
	// A server with no home configured must not panic or touch the filesystem.
	pruneTurnLogs("", "sess", 10, time.Hour, time.Now())
}
