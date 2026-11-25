package workers

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
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
func TestAStatusReadDoesNotStartTheWorker(t *testing.T) {
	s := NewLazyRMDYToolWorker(context.Background(), RMDYToolOptions{HomeDir: t.TempDir()})
	defer s.Close()
	_, err := s.Caller().Call(OnlyIfRunning(context.Background()), protocol.Frame{})
	if !errors.Is(err, ErrWorkerNotRunning) {
		t.Fatalf("err = %v, want ErrWorkerNotRunning", err)
	}
	if s.Session() != nil || s.Caller().Client() != nil {
		t.Fatal("the call must not have started a worker")
	}
}
