package httpapi

import (
	"context"
	"testing"
)

type abortingRunner struct{}

func (abortingRunner) RunTurn(ctx context.Context, _ TurnRequest, emit func(string) error) error {
	_ = emit("@@aborted\n")
	return nil // RunTurn reports a stopped turn this way, not as an error.
}

// A forager whose turn was stopped must be recorded as cancelled, not as a
// finished report with "Done" set.
func TestStoppedHiveForagerIsRecordedCancelled(t *testing.T) {
	fs := openHiveStore(t.TempDir())
	d, err := fs.hire("review auth.py", hiveCadenceForager, "", "", "", 1, 0)
	if err != nil {
		t.Fatal(err)
	}
	s := &Server{hiveFS: fs, runner: abortingRunner{}}
	if err := s.runHiveForagerPulse(context.Background(), d); err != nil {
		t.Fatal(err)
	}
	got, ok, err := fs.get(d.ID)
	if err != nil || !ok {
		t.Fatalf("get: %v %v", ok, err)
	}
	if got.Status != hiveStatusCancelled || got.Packet == nil || got.Packet.Done {
		t.Fatalf("status=%q packet=%#v", got.Status, got.Packet)
	}
}
