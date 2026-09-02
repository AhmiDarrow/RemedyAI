package scheduler

import (
	"context"
	"encoding/json"
	"errors"
	"github.com/AhmiDarrow/RemedyAI/native/go/events"
	"testing"
	"time"
)

func TestTriggersDependenciesPrioritiesAndBudgets(t *testing.T) {
	now := time.Unix(100, 0)
	var order []string
	s := New(ExecutorFunc(func(_ context.Context, job Job) error { order = append(order, job.ID); return nil }), func() time.Time { return now })
	_ = s.Add(Job{ID: "low", Trigger: OneShot, NextRun: now, Priority: 1})
	_ = s.Add(Job{ID: "high", Trigger: OneShot, NextRun: now, Priority: 9})
	_ = s.Add(Job{ID: "after", Trigger: OneShot, NextRun: now, Dependencies: []string{"high"}})
	done := s.Tick(context.Background(), now)
	if len(done) != 2 || order[0] != "high" || order[1] != "low" {
		t.Fatalf("first=%v", order)
	}
	s.Tick(context.Background(), now)
	if order[2] != "after" {
		t.Fatalf("dependency=%v", order)
	}
	_ = s.Add(Job{ID: "repeat", Trigger: Recurring, NextRun: now, Interval: time.Minute, Budget: Budget{MaxRuns: 2}})
	s.Tick(context.Background(), now)
	now = now.Add(time.Minute)
	s.Tick(context.Background(), now)
	raw, _ := s.Snapshot()
	if !json.Valid(raw) {
		t.Fatal("invalid snapshot")
	}
}

func TestEventGoalCancelDeadlineAndCycles(t *testing.T) {
	now := time.Unix(100, 0)
	s := New(ExecutorFunc(func(context.Context, Job) error { return nil }), func() time.Time { return now })
	_ = s.Add(Job{ID: "event", Trigger: OnEvent, EventType: "Wake"})
	_ = s.Add(Job{ID: "goal", Trigger: OnGoal, GoalID: "g"})
	s.HandleEvent(events.Event{Type: "Wake"})
	s.GoalReady("g")
	if len(s.Tick(context.Background(), now)) != 2 {
		t.Fatal("triggers not ready")
	}
	_ = s.Add(Job{ID: "cancel", Trigger: OneShot, NextRun: now})
	_ = s.Cancel("cancel")
	_ = s.Add(Job{ID: "late", Trigger: OneShot, NextRun: now, Deadline: now.Add(-time.Second)})
	if len(s.Tick(context.Background(), now)) != 0 {
		t.Fatal("canceled/deadline ran")
	}
	s2 := New(ExecutorFunc(func(context.Context, Job) error { return nil }), nil)
	_ = s2.Add(Job{ID: "a", Dependencies: []string{"b"}})
	if err := s2.Add(Job{ID: "b", Dependencies: []string{"a"}}); !errors.Is(err, ErrCycle) {
		t.Fatalf("cycle=%v", err)
	}
}

func TestSnapshotRestoreRecoversRunningJobs(t *testing.T) {
	now := time.Unix(100, 0)
	s := New(ExecutorFunc(func(context.Context, Job) error { return nil }), nil)
	_ = s.Add(Job{ID: "job", Trigger: OneShot, NextRun: now, Status: Running})
	raw, err := s.Snapshot()
	if err != nil {
		t.Fatal(err)
	}
	restored, err := Restore(raw, ExecutorFunc(func(context.Context, Job) error { return nil }), nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(restored.Tick(context.Background(), now)) != 1 {
		t.Fatal("running job was not recovered")
	}
}
