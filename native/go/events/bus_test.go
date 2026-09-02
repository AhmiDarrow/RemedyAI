package events

import (
	"encoding/json"
	"path/filepath"
	"sync"
	"testing"
)

func TestDurableReplayAndFilters(t *testing.T) {
	path := filepath.Join(t.TempDir(), "events.log")
	bus, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	sub := bus.Subscribe(Filter{Types: map[string]bool{"ToolCompleted": true}}, 2, DropNewest)
	first, _ := bus.Publish(Event{Type: "ToolStarted", Source: "runtime"})
	second, _ := bus.Publish(Event{Type: "ToolCompleted", Source: "runtime", Data: json.RawMessage(`{"ok":true}`)})
	if first.Sequence != 1 || second.Sequence != 2 {
		t.Fatalf("sequences=%d,%d", first.Sequence, second.Sequence)
	}
	if got := <-sub.C; got.Sequence != 2 {
		t.Fatalf("filtered=%#v", got)
	}
	sub.Close()
	if err := bus.Close(); err != nil {
		t.Fatal(err)
	}
	bus, err = Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer bus.Close()
	replayed := bus.Replay(2, 10)
	if len(replayed) != 1 || replayed[0].Type != "ToolCompleted" {
		t.Fatalf("replay=%#v", replayed)
	}
}

func TestBackpressurePoliciesAreObservable(t *testing.T) {
	bus, err := Open(filepath.Join(t.TempDir(), "events.log"))
	if err != nil {
		t.Fatal(err)
	}
	defer bus.Close()
	oldest := bus.Subscribe(Filter{}, 1, DropOldest)
	disconnected := bus.Subscribe(Filter{}, 1, Disconnect)
	for i := 0; i < 3; i++ {
		if _, err := bus.Publish(Event{Type: "Tick", Source: "timer"}); err != nil {
			t.Fatal(err)
		}
	}
	if stats := oldest.Stats(); stats.Dropped != 2 {
		t.Fatalf("oldest stats=%#v", stats)
	}
	if got := <-oldest.C; got.Sequence != 3 {
		t.Fatalf("oldest got=%d", got.Sequence)
	}
	if stats := disconnected.Stats(); !stats.Disconnected {
		t.Fatalf("disconnect stats=%#v", stats)
	}
}

func TestConcurrentPublishPreservesMonotonicLog(t *testing.T) {
	bus, err := Open(filepath.Join(t.TempDir(), "events.log"))
	if err != nil {
		t.Fatal(err)
	}
	defer bus.Close()
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if _, err := bus.Publish(Event{Type: "Concurrent", Source: "test"}); err != nil {
				t.Error(err)
			}
		}()
	}
	wg.Wait()
	events := bus.Replay(1, 200)
	if len(events) != 100 {
		t.Fatalf("events=%d", len(events))
	}
	for i, event := range events {
		if event.Sequence != uint64(i+1) {
			t.Fatalf("sequence[%d]=%d", i, event.Sequence)
		}
	}
}
