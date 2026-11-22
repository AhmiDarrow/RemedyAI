package httpapi

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"
	"time"
)

func TestAppendAndReadLearnLog(t *testing.T) {
	home := t.TempDir()
	appendLearnLog(home, "turn_end", 12*time.Millisecond, false, nil, map[string]any{"learned": true})
	appendLearnLog(home, "improve", 8*time.Millisecond, false, errors.New("boom"), nil)
	appendLearnLog(home, "after_tool_batch", 30*time.Second, true, contextDeadline(), nil)

	rows := readLearnLog(home, 20)
	if len(rows) != 3 {
		t.Fatalf("rows=%d %#v", len(rows), rows)
	}
	if rows[0]["kind"] != "turn_end" || rows[0]["ok"] != true {
		t.Fatalf("first=%v", rows[0])
	}
	if rows[1]["kind"] != "improve" || rows[1]["ok"] != false || rows[1]["error"] != "boom" {
		t.Fatalf("second=%v", rows[1])
	}
	if rows[2]["kind"] != "after_tool_batch" || rows[2]["timeout"] != true {
		t.Fatalf("third=%v", rows[2])
	}

	raw, err := os.ReadFile(filepath.Join(home, learnLogName))
	if err != nil {
		t.Fatal(err)
	}
	var first learnLogEntry
	if err := json.Unmarshal([]byte(splitFirst(raw)), &first); err != nil {
		t.Fatal(err)
	}
	if first.Kind != "turn_end" || first.DurationMS <= 0 {
		t.Fatalf("entry=%#v", first)
	}
}

func TestLearnLogRotatesWhenLarge(t *testing.T) {
	home := t.TempDir()
	path := filepath.Join(home, learnLogName)
	// Write a file just over the cap, then one more append triggers rotation.
	f, err := os.Create(path)
	if err != nil {
		t.Fatal(err)
	}
	line := []byte(`{"ts":"2026-01-01T00:00:00Z","kind":"improve","duration_ms":1,"ok":true}` + "\n")
	n := (learnLogMaxBytes / len(line)) + 10
	for i := 0; i < n; i++ {
		if _, err := f.Write(line); err != nil {
			t.Fatal(err)
		}
	}
	_ = f.Close()
	appendLearnLog(home, "turn_end", time.Millisecond, false, nil, nil)
	info, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	if info.Size() >= learnLogMaxBytes {
		t.Fatalf("log was not rotated, size=%d", info.Size())
	}
	rows := readLearnLog(home, 500)
	if len(rows) == 0 {
		t.Fatal("rotated log is empty")
	}
	if rows[len(rows)-1]["kind"] != "turn_end" {
		t.Fatalf("last=%v", rows[len(rows)-1])
	}
}

func TestReadLearnLogMissingIsEmpty(t *testing.T) {
	rows := readLearnLog(t.TempDir(), 10)
	if rows == nil || len(rows) != 0 {
		t.Fatalf("rows=%v", rows)
	}
}

func contextDeadline() error {
	return errDeadline
}

var errDeadline = deadlineErr{}

type deadlineErr struct{}

func (deadlineErr) Error() string { return "context deadline exceeded" }
func (deadlineErr) Timeout() bool { return true }

func splitFirst(raw []byte) string {
	for i, b := range raw {
		if b == '\n' {
			return string(raw[:i])
		}
	}
	return string(raw)
}
