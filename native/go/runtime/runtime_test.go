package runtime

import (
	"context"
	"errors"
	"fmt"
	"sync"
	"sync/atomic"
	"testing"
	"time"
)

func TestLifecycleAndCancellation(t *testing.T) {
	r := New()
	if err := r.Go("early", func(context.Context) error { return nil }); !errors.Is(err, ErrNotRunning) {
		t.Fatalf("Go before Start: %v", err)
	}
	if err := r.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	stopped := make(chan struct{})
	if err := r.Go("watcher", func(ctx context.Context) error { <-ctx.Done(); close(stopped); return ctx.Err() }); err != nil {
		t.Fatal(err)
	}
	if err := r.Stop(); err != nil {
		t.Fatal(err)
	}
	select {
	case <-stopped:
	case <-time.After(time.Second):
		t.Fatal("worker did not observe cancellation")
	}
	if r.State() != StateStopped {
		t.Fatalf("state = %v", r.State())
	}
}

func TestSupervisionRestartsAndReportsStructuredFailure(t *testing.T) {
	r := New()
	if err := r.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	var attempts atomic.Int32
	if err := r.Supervise(WorkerSpec{Name: "recovering", Run: func(context.Context) error {
		if attempts.Add(1) < 3 {
			return errors.New("transient")
		}
		return nil
	}, Restart: RestartPolicy{MaxRestarts: 2}}); err != nil {
		t.Fatal(err)
	}
	for expected := 1; expected <= 2; expected++ {
		select {
		case failure := <-r.Failures():
			if failure.Worker != "recovering" || failure.Attempt != expected || failure.Panic {
				t.Fatalf("unexpected failure: %+v", failure)
			}
		case <-time.After(time.Second):
			t.Fatal("missing failure evidence")
		}
	}
	if err := r.Stop(); err != nil {
		t.Fatal(err)
	}
}

func TestFatalPanicCancelsRuntimeWithCause(t *testing.T) {
	r := New()
	if err := r.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	ctx, err := r.Context()
	if err != nil {
		t.Fatal(err)
	}
	if err := r.Supervise(WorkerSpec{Name: "critical", Fatal: true, Run: func(context.Context) error { panic("boom") }}); err != nil {
		t.Fatal(err)
	}
	select {
	case <-ctx.Done():
		if context.Cause(ctx) == nil {
			t.Fatal("missing cancellation cause")
		}
	case <-time.After(time.Second):
		t.Fatal("fatal worker did not cancel runtime")
	}
	if err := r.Stop(); err != nil {
		t.Fatal(err)
	}
}

func TestConcurrentWorkerRegistrationAndStop(t *testing.T) {
	r := New()
	if err := r.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	var callers sync.WaitGroup
	for i := 0; i < 64; i++ {
		callers.Add(1)
		go func(i int) {
			defer callers.Done()
			_ = r.Go(fmt.Sprintf("worker-%d", i), func(ctx context.Context) error { <-ctx.Done(); return ctx.Err() })
		}(i)
	}
	callers.Wait()
	if err := r.Stop(); err != nil {
		t.Fatal(err)
	}
	if err := r.Go("late", func(context.Context) error { return nil }); !errors.Is(err, ErrNotRunning) {
		t.Fatalf("late worker: %v", err)
	}
}

func TestDuplicateWorkerNameRejectedWhileActive(t *testing.T) {
	r := New()
	if err := r.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := r.Go("same", func(ctx context.Context) error { <-ctx.Done(); return ctx.Err() }); err != nil {
		t.Fatal(err)
	}
	if err := r.Go("same", func(context.Context) error { return nil }); !errors.Is(err, ErrWorkerExists) {
		t.Fatalf("duplicate worker: %v", err)
	}
	if err := r.Stop(); err != nil {
		t.Fatal(err)
	}
}

func TestStopTimeoutEventuallyReachesStopped(t *testing.T) {
	r := New()
	if err := r.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	release := make(chan struct{})
	if err := r.Go("slow", func(context.Context) error { <-release; return nil }); err != nil {
		t.Fatal(err)
	}
	wait, cancel := context.WithTimeout(context.Background(), time.Millisecond)
	defer cancel()
	if err := r.StopContext(wait, errors.New("shutdown")); !errors.Is(err, context.DeadlineExceeded) {
		t.Fatalf("StopContext timeout: %v", err)
	}
	close(release)
	deadline := time.Now().Add(time.Second)
	for r.State() != StateStopped && time.Now().Before(deadline) {
		time.Sleep(time.Millisecond)
	}
	if r.State() != StateStopped {
		t.Fatalf("state = %v", r.State())
	}
}
