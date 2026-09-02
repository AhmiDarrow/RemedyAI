package runtime

import (
	"context"
	"errors"
	"strings"
	"testing"
	"time"
)

func TestLifecycleAndCancellation(t *testing.T) {
	r := New()
	if err := r.Go("early", func(context.Context) error { return nil }); !errors.Is(err, ErrNotStarted) {
		t.Fatalf("Go before Start: %v", err)
	}
	if err := r.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	stopped := make(chan struct{})
	if err := r.Go("watcher", func(ctx context.Context) error {
		<-ctx.Done()
		close(stopped)
		return ctx.Err()
	}); err != nil {
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

func TestWorkerFailureAndPanicAreEvidence(t *testing.T) {
	r := New()
	if err := r.Start(context.Background()); err != nil {
		t.Fatal(err)
	}
	if err := r.Go("failure", func(context.Context) error { return errors.New("broken") }); err != nil {
		t.Fatal(err)
	}
	if err := r.Go("panic", func(context.Context) error { panic("boom") }); err != nil {
		t.Fatal(err)
	}
	seen := ""
	for range 2 {
		select {
		case err := <-r.Errors():
			seen += err.Error()
		case <-time.After(time.Second):
			t.Fatal("missing worker evidence")
		}
	}
	if !strings.Contains(seen, "broken") || !strings.Contains(seen, "panicked") {
		t.Fatalf("unexpected evidence: %q", seen)
	}
	if err := r.Stop(); err != nil {
		t.Fatal(err)
	}
}
