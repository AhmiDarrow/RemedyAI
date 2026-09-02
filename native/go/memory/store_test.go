package memory

import (
	"errors"
	"os"
	"path/filepath"
	"sync"
	"testing"
	"time"
)

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
