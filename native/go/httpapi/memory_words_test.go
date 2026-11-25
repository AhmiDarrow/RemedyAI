package httpapi

import (
	"path/filepath"
	"testing"
)

func TestMemoryQueryWordsDropsFillerAndRepeats(t *testing.T) {
	got := memoryQueryWords("What did we decide about the garden fence, the FENCE?")
	want := []string{"decide", "garden", "fence"}
	if len(got) != len(want) {
		t.Fatalf("words = %v, want %v", got, want)
	}
	for i := range want {
		if got[i] != want[i] {
			t.Fatalf("words = %v, want %v", got, want)
		}
	}
	if len(memoryQueryWords("a of to")) != 0 {
		t.Fatal("a query of filler words has nothing to match")
	}
}

func TestSearchMemoryWordsRanksByWordsMatched(t *testing.T) {
	store, err := openSessionStore(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer store.Close()
	if err := store.ensurePartnerMemorySchema(); err != nil {
		t.Fatal(err)
	}
	insert := func(id, title, content, created string) {
		t.Helper()
		if _, err := store.db.Exec(
			`INSERT INTO memory_entries (id, title, content, entry_type, importance, created_at, updated_at, metadata)
			 VALUES (?, ?, ?, 'note', 0.5, ?, ?, '{}')`, id, title, content, created, created); err != nil {
			t.Fatal(err)
		}
	}
	insert("a", "Fence", "The garden fence gets cedar posts.", "2026-01-01T00:00:00Z")
	insert("b", "Garden", "Tomatoes go in the garden in May.", "2026-02-01T00:00:00Z")
	insert("c", "Car", "Oil change every 5000 miles. 100% sure_thing.", "2026-03-01T00:00:00Z")

	// The whole question is not in any note; the old phrase match found nothing.
	if rows, _ := store.searchMemoryLIKE("what did we decide about the garden fence", 10); len(rows) != 0 {
		t.Fatalf("phrase match unexpectedly found %d rows", len(rows))
	}
	rows, err := store.searchMemoryWords("what did we decide about the garden fence", 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(rows) != 2 || rows[0].ID != "a" || rows[1].ID != "b" {
		t.Fatalf("want the two-word match first, then the one-word match, got %+v", rows)
	}
	// Punctuation splits words, so SQL wildcards in a query never reach the pattern.
	if rows, _ := store.searchMemoryWords("100% sure_thing", 10); len(rows) != 1 || rows[0].ID != "c" {
		t.Fatalf("want the car note, got %+v", rows)
	}
	if rows, _ := store.searchMemoryWords("%_% xyzzy", 10); len(rows) != 0 {
		t.Fatalf("a query with no matching word finds nothing, got %+v", rows)
	}
}
