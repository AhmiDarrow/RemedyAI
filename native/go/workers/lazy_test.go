package workers

import (
	"context"
	"testing"
	"time"
)

func TestLazyWorkerRunsNothingUntilCalled(t *testing.T) {
	s := NewLazyRMDYToolWorker(context.Background(), RMDYToolOptions{HomeDir: t.TempDir()})
	if state, _ := ToolsPythonStatus(); state != ToolsPythonIdle {
		t.Fatalf("state = %q, want idle", state)
	}
	if s.Session() != nil || s.Caller().Client() != nil {
		t.Fatal("a lazy supervisor must not have started a worker")
	}
	closed := make(chan error, 1)
	go func() { closed <- s.Close() }()
	select {
	case err := <-closed:
		if err != nil {
			t.Fatalf("close: %v", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("closing a supervisor that never started must not hang")
	}
	// After Close a call must fail cleanly instead of starting a worker.
	if err := s.startOnDemand(); err == nil {
		t.Fatal("a closed supervisor must not start a worker")
	}
}