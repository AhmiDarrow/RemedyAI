package httpapi

import (
	"os"
	"sort"
	"strings"
	"sync"
)

// The Skills Library can hold thousands of entries. The catalog file is
// verified and parsed once, when it changes, and kept in memory with what a
// search needs already prepared. A search then touches no file, and callers
// get one page of ranked results instead of the whole list.

// libraryIndexed is one catalog entry with its searchable text prepared.
type libraryIndexed struct {
	entry  librarySkillEntry
	name   string // lower case
	desc   string // lower case
	tags   []string
	author string
	tokens map[string]struct{} // name, description, tags and tools
	nameT  map[string]struct{}
	tagT   map[string]struct{}
}

type libraryIndex struct {
	cat     skillsCatalog
	entries []libraryIndexed
}

var libraryMemo struct {
	sync.Mutex
	key string
	idx *libraryIndex
}

// libraryIndexFromCache returns the index of the cached catalog, building it
// only when the file (or the key it is verified with) changed. ok is false
// when there is no cached catalog that verifies.
func libraryIndexFromCache(cacheFile, cacheSig, pub string) (*libraryIndex, bool) {
	st, err := os.Stat(cacheFile)
	if err != nil {
		return nil, false
	}
	sigSt, err := os.Stat(cacheSig)
	if err != nil {
		return nil, false
	}
	key := strings.Join([]string{
		cacheFile, st.ModTime().String(), sigSt.ModTime().String(), pub,
	}, "|") + "|" + itoa64(st.Size())
	libraryMemo.Lock()
	defer libraryMemo.Unlock()
	if libraryMemo.idx != nil && libraryMemo.key == key {
		return libraryMemo.idx, true
	}
	data, err := os.ReadFile(cacheFile)
	if err != nil {
		return nil, false
	}
	sig, err := os.ReadFile(cacheSig)
	if err != nil || verifyCatalogSignature(data, string(sig), pub) != nil {
		return nil, false
	}
	cat, err := loadCatalogFromBytes(data, "cache")
	if err != nil {
		return nil, false
	}
	idx := newLibraryIndex(cat)
	libraryMemo.key, libraryMemo.idx = key, idx
	return idx, true
}

func itoa64(n int64) string {
	if n == 0 {
		return "0"
	}
	var b [20]byte
	i := len(b)
	for n > 0 {
		i--
		b[i] = byte('0' + n%10)
		n /= 10
	}
	return string(b[i:])
}

func newLibraryIndex(cat skillsCatalog) *libraryIndex {
	idx := &libraryIndex{cat: cat, entries: make([]libraryIndexed, 0, len(cat.Skills))}
	for _, e := range cat.Skills {
		name := strings.ToLower(e.Name)
		spaced := strings.NewReplacer("-", " ", "_", " ").Replace(name)
		it := libraryIndexed{
			entry: e, name: name, desc: strings.ToLower(e.Description),
			author: strings.ToLower(e.Author),
			nameT:  tokenizeSkillText(spaced),
			tagT:   tokenizeSkillText(strings.Join(e.Tags, " ")),
		}
		for _, t := range e.Tags {
			it.tags = append(it.tags, strings.ToLower(t))
		}
		it.tokens = tokenizeSkillText(spaced + " " + e.Description + " " +
			strings.Join(e.Tags, " ") + " " + strings.Join(e.Tools, " "))
		idx.entries = append(idx.entries, it)
	}
	return idx
}

// libraryQuery is one search: free text, filters, an order and a page.
type libraryQuery struct {
	Text    string
	Tags    []string // lower case; an entry needs one of them
	Author  string
	SortBy  string // "", "name", "rating", "updated_at", "installs"
	Offset  int
	Limit   int // 0 = everything from Offset
	Exclude map[string]struct{}
}

// search returns one page of matches and how many matched in all. With text
// and no explicit order the best matches come first: a hit in the name counts
// most, then a tag, then the description.
func (idx *libraryIndex) search(q libraryQuery) ([]librarySkillEntry, int) {
	text := strings.ToLower(strings.TrimSpace(q.Text))
	words := tokenizeSkillText(text)
	type hit struct {
		at    int
		score float64
	}
	hits := make([]hit, 0, 64)
	for i := range idx.entries {
		it := &idx.entries[i]
		if q.Exclude != nil {
			if _, skip := q.Exclude[it.entry.ID]; skip {
				continue
			}
		}
		if q.Author != "" && it.author != strings.ToLower(q.Author) {
			continue
		}
		if len(q.Tags) > 0 && !anyTag(it.tags, q.Tags) {
			continue
		}
		score := 0.0
		if text != "" {
			switch {
			case it.name == text:
				score += 12
			case strings.HasPrefix(it.name, text):
				score += 8
			case strings.Contains(it.name, text):
				score += 6
			}
			if strings.Contains(it.desc, text) {
				score += 2
			}
			for _, tag := range it.tags {
				if strings.Contains(tag, text) {
					score += 3
					break
				}
			}
			for w := range words {
				if _, ok := it.nameT[w]; ok {
					score += 3
				} else if _, ok := it.tagT[w]; ok {
					score += 2
				} else if _, ok := it.tokens[w]; ok {
					score++
				}
			}
			if score == 0 {
				continue
			}
		}
		hits = append(hits, hit{i, score})
	}
	less := func(a, b hit) bool { return idx.entries[a.at].name < idx.entries[b.at].name }
	sort.SliceStable(hits, func(i, j int) bool {
		a, b := hits[i], hits[j]
		ea, eb := idx.entries[a.at].entry, idx.entries[b.at].entry
		switch q.SortBy {
		case "rating":
			if ea.Rating != eb.Rating {
				return ea.Rating > eb.Rating
			}
		case "updated_at":
			if ea.UpdatedAt != eb.UpdatedAt {
				return ea.UpdatedAt > eb.UpdatedAt
			}
		case "installs":
			if ea.Installs != eb.Installs {
				return ea.Installs > eb.Installs
			}
		case "name":
		default:
			if a.score != b.score {
				return a.score > b.score
			}
		}
		return less(a, b)
	})
	total := len(hits)
	start := min(max(q.Offset, 0), total)
	end := total
	if q.Limit > 0 {
		end = min(start+q.Limit, total)
	}
	out := make([]librarySkillEntry, 0, end-start)
	for _, h := range hits[start:end] {
		out = append(out, idx.entries[h.at].entry)
	}
	return out, total
}

func anyTag(have, want []string) bool {
	for _, t := range have {
		for _, w := range want {
			if t == w {
				return true
			}
		}
	}
	return false
}

// installedSkillsMemo keeps the parsed installed skills until a skill folder
// changes, so a lookup does not read and parse every SKILL.md again.
var installedSkillsMemo struct {
	sync.Mutex
	key  string
	recs []skillRecord
}

// skillRootsSignature changes when a skill is added, removed or edited in any
// of the roots: each skill folder's SKILL.md size and change time.
func skillRootsSignature(roots []string) string {
	var b strings.Builder
	for _, root := range roots {
		b.WriteString(root)
		b.WriteByte('\n')
		entries, err := os.ReadDir(root)
		if err != nil {
			continue
		}
		for _, ent := range entries {
			if !ent.IsDir() || strings.HasPrefix(ent.Name(), ".") {
				continue
			}
			st, err := os.Stat(root + string(os.PathSeparator) + ent.Name() + string(os.PathSeparator) + "SKILL.md")
			if err != nil {
				continue
			}
			b.WriteString(ent.Name())
			b.WriteByte('|')
			b.WriteString(itoa64(st.Size()))
			b.WriteByte('|')
			b.WriteString(itoa64(st.ModTime().UnixNano()))
			b.WriteByte('\n')
		}
	}
	return b.String()
}
