package memory

import (
	"errors"
	"io"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

var errInjectedWrite = errors.New("injected partial write")

type partialAppendFile struct {
	*os.File
	remaining int
}

type firstSyncFailsFile struct {
	*os.File
	syncs int
}

func (f *firstSyncFailsFile) Sync() error {
	f.syncs++
	if f.syncs == 1 {
		return errors.New("injected sync failure")
	}
	return f.File.Sync()
}

func (f *partialAppendFile) Write(payload []byte) (int, error) {
	if f.remaining <= 0 {
		return 0, errInjectedWrite
	}
	if len(payload) <= f.remaining {
		n, err := f.File.Write(payload)
		f.remaining -= n
		return n, err
	}
	n, _ := f.File.Write(payload[:f.remaining])
	f.remaining -= n
	return n, errInjectedWrite
}

func TestStoreDurabilityTypesAndRetrieval(t *testing.T) {
	path := filepath.Join(t.TempDir(), "memory.log")
	store, err := Open(path)
	if err != nil {
		t.Fatal(err)
	}
	now := time.Unix(10, 0).UTC()
	for i, kind := range []Kind{Episodic, Semantic, Procedural, Working, Relational} {
		record := Record{ID: string(rune('a' + i)), Namespace: "owner", Kind: kind, Key: "remedy", Content: "native continuity", CreatedAt: now.Add(time.Duration(i) * time.Second)}
		if err := store.Append(record); err != nil {
			t.Fatal(err)
		}
	}
	if err := store.Close(); err != nil {
		t.Fatal(err)
	}
	store, err = Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	matches := store.Search(Query{Namespace: "owner", Text: "continuity", Limit: 3})
	if len(matches) != 3 {
		t.Fatalf("matches=%d", len(matches))
	}
	if got, ok := store.GetLatest("owner", Relational, "remedy"); !ok || got.Kind != Relational {
		t.Fatalf("latest=%#v ok=%v", got, ok)
	}
}

func TestStoreRecoversTruncatedTail(t *testing.T) {
	path := filepath.Join(t.TempDir(), "memory.log")
	store, _ := Open(path)
	record := Record{ID: "1", Namespace: "n", Kind: Episodic, Key: "k", Content: "v", CreatedAt: time.Now()}
	if err := store.Append(record); err != nil {
		t.Fatal(err)
	}
	store.Close()
	file, err := os.OpenFile(path, os.O_APPEND|os.O_WRONLY, 0)
	if err != nil {
		t.Fatal(err)
	}
	_, _ = file.Write([]byte{20, 0, 0, 0, 1})
	file.Close()
	store, err = Open(path)
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if _, ok := store.GetLatest("n", Episodic, "k"); !ok {
		t.Fatal("valid record was lost")
	}
}

func TestStoreRejectsChecksumCorruption(t *testing.T) {
	path := filepath.Join(t.TempDir(), "memory.log")
	store, _ := Open(path)
	_ = store.Append(Record{ID: "1", Namespace: "n", Kind: Semantic, Key: "k", Content: "v", CreatedAt: time.Now()})
	store.Close()
	data, _ := os.ReadFile(path)
	data[len(data)-1] ^= 1
	if err := os.WriteFile(path, data, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := Open(path); !errors.Is(err, ErrCorruptLog) {
		t.Fatalf("Open=%v", err)
	}
}

func TestStoreConcurrentReaders(t *testing.T) {
	store, err := Open(filepath.Join(t.TempDir(), "memory.log"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	_ = store.Append(Record{ID: "1", Namespace: "n", Kind: Working, Key: "k", Content: "search me", CreatedAt: time.Now()})
	var wg sync.WaitGroup
	for i := 0; i < 100; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			if len(store.Search(Query{Namespace: "n", Text: "search"})) != 1 {
				t.Error("missing result")
			}
		}()
	}
	wg.Wait()
}

func TestAppendFrameRollsBackPartialWriteBeforeNextAppend(t *testing.T) {
	path := filepath.Join(t.TempDir(), "partial.log")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	if _, err := file.Write([]byte("valid-prefix")); err != nil {
		t.Fatal(err)
	}
	failing := &partialAppendFile{File: file, remaining: 10}
	if err := appendFrame(failing, []byte("12345678"), []byte("payload")); !errors.Is(err, errInjectedWrite) {
		t.Fatalf("appendFrame error = %v", err)
	}
	position, err := file.Seek(0, io.SeekCurrent)
	if err != nil {
		t.Fatal(err)
	}
	if position != int64(len("valid-prefix")) {
		t.Fatalf("position after rollback = %d", position)
	}
	if err := appendFrame(file, []byte("next")); err != nil {
		t.Fatal(err)
	}
	data, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if string(data) != "valid-prefixnext" {
		t.Fatalf("contents after recovery = %q", data)
	}
}

func TestAppendFramePersistsRollbackAfterSyncFailure(t *testing.T) {
	path := filepath.Join(t.TempDir(), "sync.log")
	file, err := os.OpenFile(path, os.O_CREATE|os.O_RDWR, 0o600)
	if err != nil {
		t.Fatal(err)
	}
	defer file.Close()
	failing := &firstSyncFailsFile{File: file}
	if err := appendFrame(failing, []byte("not-durable")); err == nil {
		t.Fatal("sync failure was accepted")
	}
	if failing.syncs != 2 {
		t.Fatalf("sync calls = %d, want initial + rollback", failing.syncs)
	}
	info, err := file.Stat()
	if err != nil {
		t.Fatal(err)
	}
	if info.Size() != 0 {
		t.Fatalf("rollback size = %d", info.Size())
	}
}
