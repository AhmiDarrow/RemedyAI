package httpapi

import (
	"fmt"
	"testing"
)

func libraryTestIndex(n int) *libraryIndex {
	cat := skillsCatalog{Skills: []librarySkillEntry{
		{ID: "git-bisect-helper", Name: "git-bisect-helper", Description: "Find the commit that broke a test", Tags: []string{"git", "debugging"}},
		{ID: "branch-hygiene", Name: "branch-hygiene", Description: "Keep git branches tidy", Tags: []string{"git"}},
		{ID: "recipe-plan-meals", Name: "recipe-plan-meals", Description: "Plan a week of meals", Tags: []string{"home"}, Author: "Ahmi"},
	}}
	for i := 0; i < n; i++ {
		cat.Skills = append(cat.Skills, librarySkillEntry{
			ID: fmt.Sprintf("filler-%04d", i), Name: fmt.Sprintf("filler-%04d", i), Description: "Nothing to do with it",
		})
	}
	return newLibraryIndex(cat)
}

func TestLibrarySearchRanksNameAboveDescriptionAndPages(t *testing.T) {
	idx := libraryTestIndex(2000)
	got, total := idx.search(libraryQuery{Text: "git", Limit: 10})
	if total != 2 || len(got) != 2 {
		t.Fatalf("total=%d got=%v", total, got)
	}
	// "git" is in the first one's name; in the second only in a tag and the text.
	if got[0].ID != "git-bisect-helper" || got[1].ID != "branch-hygiene" {
		t.Fatalf("order: %s, %s", got[0].ID, got[1].ID)
	}
	if hits, n := idx.search(libraryQuery{Text: "zzzz-no-such"}); n != 0 || len(hits) != 0 {
		t.Fatalf("no match expected: %d %v", n, hits)
	}

	// No text: everything, by name, a page at a time.
	page, all := idx.search(libraryQuery{SortBy: "name", Limit: 40, Offset: 80})
	if all != 2003 || len(page) != 40 {
		t.Fatalf("page of %d from %d", len(page), all)
	}
	last, _ := idx.search(libraryQuery{SortBy: "name", Limit: 40, Offset: 2000})
	if len(last) != 3 {
		t.Fatalf("the last page holds what is left: %d", len(last))
	}
	if beyond, _ := idx.search(libraryQuery{Limit: 40, Offset: 99999}); len(beyond) != 0 {
		t.Fatalf("past the end: %d", len(beyond))
	}
}

func TestLibrarySearchFiltersAndExcludes(t *testing.T) {
	idx := libraryTestIndex(0)
	if got, n := idx.search(libraryQuery{Tags: []string{"home"}}); n != 1 || got[0].ID != "recipe-plan-meals" {
		t.Fatalf("tag filter: %d %v", n, got)
	}
	if _, n := idx.search(libraryQuery{Author: "ahmi"}); n != 1 {
		t.Fatalf("author filter: %d", n)
	}
	got, n := idx.search(libraryQuery{Text: "git", Exclude: map[string]struct{}{"git-bisect-helper": {}}})
	if n != 1 || got[0].ID != "branch-hygiene" {
		t.Fatalf("installed skills are left out: %d %v", n, got)
	}
}

func BenchmarkLibrarySearch5000(b *testing.B) {
	idx := libraryTestIndex(5000)
	b.ResetTimer()
	for i := 0; i < b.N; i++ {
		idx.search(libraryQuery{Text: "git bisect", Limit: 20})
	}
}
