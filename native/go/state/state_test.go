package state

import (
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/events"
)

func mutationEvent(sequence uint64, area, key string, value any) events.Event {
	raw, _ := json.Marshal(value)
	data, _ := json.Marshal(Mutation{Area: area, Key: key, Value: raw})
	return events.Event{Sequence: sequence, Type: "StateChanged", Source: "runtime", Data: data}
}

func TestApplyRejectsSequenceGapWithoutAdvancingState(t *testing.T) {
	store, _ := Open(filepath.Join(t.TempDir(), "state.json"))
	if err := store.Apply(mutationEvent(1, "environment", "one", "applied")); err != nil {
		t.Fatal(err)
	}
	if err := store.Apply(mutationEvent(3, "environment", "three", "skipped")); !errors.Is(err, ErrEventGap) {
		t.Fatalf("Apply = %v", err)
	}
	snapshot := store.Snapshot()
	if snapshot.LastEvent != 1 || snapshot.Environment["three"] != "" {
		t.Fatalf("snapshot = %#v", snapshot)
	}
	if err := store.Apply(mutationEvent(2, "environment", "two", "recovered")); err != nil {
		t.Fatal(err)
	}
}

func TestCheckpointRestartAndIdempotentReplay(t *testing.T) {
	path := filepath.Join(t.TempDir(), "state.json")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	event := mutationEvent(1, "goal", "g1", Goal{ID: "g1", Description: "finish", Status: "active"})
	if err := store.Apply(event); err != nil {
		t.Fatal(err)
	}
	if err := store.Apply(event); err != nil {
		t.Fatal(err)
	}
	if err := store.Checkpoint(); err != nil {
		t.Fatal(err)
	}
	if err := store.Checkpoint(); err != nil {
		t.Fatal("replace checkpoint:", err)
	}
	store, err = Open(path)
	if err != nil {
		t.Fatal(err)
	}
	snapshot := store.Snapshot()
	if snapshot.LastEvent != 1 || len(snapshot.Goals) != 1 {
		t.Fatalf("snapshot=%#v", snapshot)
	}
	if err := store.Replay([]events.Event{event, mutationEvent(2, "environment", "cwd", "workspace")}); err != nil {
		t.Fatal(err)
	}
	if got := store.Snapshot().Environment["cwd"]; got != "workspace" {
		t.Fatalf("cwd=%q", got)
	}
}

func TestSnapshotIsolationAndEveryStateArea(t *testing.T) {
	store, _ := Open(filepath.Join(t.TempDir(), "state.json"))
	eventsToApply := []events.Event{mutationEvent(1, "task", "t", Task{ID: "t", GoalID: "g", Status: "running"}), mutationEvent(2, "relationship", "owner", "trusted"), mutationEvent(3, "self", "mode", "ready")}
	if err := store.Replay(eventsToApply); err != nil {
		t.Fatal(err)
	}
	snapshot := store.Snapshot()
	snapshot.Tasks["x"] = Task{ID: "x"}
	if len(store.Snapshot().Tasks) != 1 {
		t.Fatal("snapshot mutated store")
	}
	if snapshot.Relationships["owner"] != "trusted" || snapshot.Self["mode"] != "ready" {
		t.Fatalf("snapshot=%#v", snapshot)
	}
}

func TestFutureSchemaRejectedAndAbruptTempIgnored(t *testing.T) {
	dir := t.TempDir()
	path := filepath.Join(dir, "state.json")
	if err := os.WriteFile(path, []byte(`{"version":99}`), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(path); !errors.Is(err, ErrUnsupportedVersion) {
		t.Fatalf("Open=%v", err)
	}
	_ = os.Remove(path)
	if err := os.WriteFile(filepath.Join(dir, ".remedy-state-crash.tmp"), []byte("partial"), 0o600); err != nil {
		t.Fatal(err)
	}
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	if store.Snapshot().Version != Version {
		t.Fatal("temp file affected recovery")
	}
}
