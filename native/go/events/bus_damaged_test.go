package events

import (
	"os"
	"path/filepath"
	"testing"
)

// A process stopped in the middle of a write leaves bytes that are not a
// record. The log must open with everything before them, not refuse to start.
func TestOpenKeepsTheGoodPartOfADamagedLog(t *testing.T) {
	path := filepath.Join(t.TempDir(), "events.log")
	bus, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	for i := 0; i < 3; i++ {
		if _, err := bus.Publish(Event{Type: "t", Source: "test"}); err != nil {
			t.Fatal(err)
		}
	}
	if err := bus.Close(); err != nil {
		t.Fatal(err)
	}
	good, err := os.Stat(path)
	if err != nil {
		t.Fatal(err)
	}
	f, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	// The tail of a payload with no header in front of it.
	if _, err := f.WriteString(`:02:44Z","data":{"half":"a record"}}` + "\x00\x00garbage"); err != nil {
		t.Fatal(err)
	}
	f.Close()

	bus, err = Open(path)
	if err != nil {
		t.Fatalf("a damaged tail must not stop the log from opening: %v", err)
	}
	defer bus.Close()
	if got := bus.Replay(0, 10); len(got) != 3 {
		t.Fatalf("kept %d events, want the 3 good ones", len(got))
	}
	next, err := bus.Publish(Event{Type: "t", Source: "test"})
	if err != nil || next.Sequence != 4 {
		t.Fatalf("the log goes on from the good part: seq=%d err=%v", next.Sequence, err)
	}
	if info, _ := os.Stat(path); info.Size() <= good.Size() {
		t.Fatalf("the new event was not appended after the good part")
	}
	set, _ := filepath.Glob(path + ".damaged-*")
	if len(set) != 1 {
		t.Fatalf("the damaged bytes are set aside, not thrown away: %v", set)
	}
}
