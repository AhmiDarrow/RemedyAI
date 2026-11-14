package secret

import (
	"bytes"
	"fmt"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

func TestWriteFileAtomicReplacesContent(t *testing.T) {
	path := filepath.Join(t.TempDir(), "job.json")
	if err := WriteFileAtomic(path, []byte("one"), 0o600); err != nil {
		t.Fatal(err)
	}
	if err := WriteFileAtomic(path, []byte("two"), 0o600); err != nil {
		t.Fatal(err)
	}
	got, err := os.ReadFile(path)
	if err != nil || string(got) != "two" {
		t.Fatalf("content=%q err=%v", got, err)
	}
}

// A reader that keeps opening the destination must not make writes fail. On
// Windows, Go opens files without FILE_SHARE_DELETE, so the replace has to
// wait out each read instead of dropping the write.
func TestWriteFileAtomicSurvivesConcurrentReaders(t *testing.T) {
	path := filepath.Join(t.TempDir(), "job.json")
	if err := WriteFileAtomic(path, []byte("v0"), 0o600); err != nil {
		t.Fatal(err)
	}
	stop := make(chan struct{})
	var wg sync.WaitGroup
	for r := 0; r < 4; r++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			for {
				select {
				case <-stop:
					return
				default:
				}
				_, _ = os.ReadFile(path)
			}
		}()
	}
	var last []byte
	for i := 1; i <= 200; i++ {
		last = []byte(fmt.Sprintf("v%d", i))
		if err := WriteFileAtomic(path, last, 0o600); err != nil {
			close(stop)
			wg.Wait()
			t.Fatalf("write %d failed with readers active: %v", i, err)
		}
	}
	close(stop)
	wg.Wait()
	got, err := os.ReadFile(path)
	if err != nil || !bytes.Equal(got, last) {
		t.Fatalf("content=%q want %q err=%v", got, last, err)
	}
}

// Holding the destination open briefly delays the replace rather than failing it.
func TestWriteFileAtomicWaitsForHeldDestination(t *testing.T) {
	path := filepath.Join(t.TempDir(), "job.json")
	if err := WriteFileAtomic(path, []byte("old"), 0o600); err != nil {
		t.Fatal(err)
	}
	f, err := os.Open(path)
	if err != nil {
		t.Fatal(err)
	}
	go func() {
		time.Sleep(150 * time.Millisecond)
		_ = f.Close()
	}()
	if err := WriteFileAtomic(path, []byte("new"), 0o600); err != nil {
		t.Fatalf("replace with a held reader: %v", err)
	}
	got, _ := os.ReadFile(path)
	if string(got) != "new" {
		t.Fatalf("content=%q", got)
	}
}
