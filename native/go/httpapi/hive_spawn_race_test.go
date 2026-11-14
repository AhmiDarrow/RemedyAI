package httpapi

import (
	"sync"
	"testing"
)

// A forager can report while the spawn handler is still committing "running".
// The commit must never overwrite that report with a stale copy.
func TestHiveSpawnCommitNeverClobbersAReport(t *testing.T) {
	for i := 0; i < 200; i++ {
		fs := openHiveStore(t.TempDir())
		d, err := fs.hire("review auth.py", hiveCadenceForager, "", "", "", 1, 0)
		if err != nil {
			t.Fatal(err)
		}
		s := &Server{hiveFS: fs}
		var wg sync.WaitGroup
		wg.Add(2)
		go func() {
			defer wg.Done()
			s.finishHiveForager(d, hivePacket{Goal: d.Goal, Outcome: "Hello", Done: true}, hiveStatusReported)
		}()
		go func() {
			defer wg.Done()
			commitHiveSpawnStart(fs, d, hiveCadenceForager, true)
		}()
		wg.Wait()
		got, ok, err := fs.get(d.ID)
		if err != nil || !ok {
			t.Fatalf("get: %v %v", ok, err)
		}
		if got.Status != hiveStatusReported {
			t.Fatalf("iteration %d: report clobbered, status=%q", i, got.Status)
		}
	}
}
