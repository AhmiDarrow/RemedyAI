package httpapi

import (
	"database/sql"
	"encoding/json"
	"math"
	"path/filepath"
	"regexp"
	"strings"
	"testing"
	"time"
)

// Store-level ports of tests/test_memory.py, test_memory_batch.py,
// test_phase1.py (consolidator / auto-handoff / repair), test_memory_authority.py
// and the extraction cases of test_partner_memory.py / test_living_memory.py,
// for the CLI's Python-compatible store.

func cliStoreOpenT(t *testing.T) *CLIStore {
	t.Helper()
	s, err := OpenCLIStore(filepath.Join(t.TempDir(), "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(func() { _ = s.Close() })
	return s
}

func cliStoreAdd(t *testing.T, s *CLIStore, entryType, title, content, session string, tags []string, imp float64) *CLIMemoryEntry {
	t.Helper()
	e := NewCLIMemoryEntry(entryType, title, content, tags, imp)
	if session != "" {
		e.SessionID = &session
	}
	if err := s.Upsert(e); err != nil {
		t.Fatal(err)
	}
	return e
}

// The row the CLI writes is the row Python's MemoryStore writes: same
// columns, json.dumps tags (", " separator, ASCII escapes), a MemoryEntryType
// value, isoformat timestamps, and the FTS index kept in sync by the schema's
// triggers.
func TestCLIStoreRowShapeMatchesPython(t *testing.T) {
	s := cliStoreOpenT(t)
	e := cliStoreAdd(t, s, "note", "Café rules", "body", "", []string{"health", "café"}, 0.8)

	cols, err := s.columns("memory_entries")
	if err != nil {
		t.Fatal(err)
	}
	for _, c := range []string{"id", "entry_type", "title", "content", "tags", "metadata", "created_at", "updated_at", "session_id", "importance"} {
		if !cols[c] {
			t.Fatalf("memory_entries lacks %s", c)
		}
	}
	var id, et, tags, meta, created, updated string
	var sid sql.NullString
	var imp float64
	if err := s.db.QueryRow(`SELECT id, entry_type, tags, metadata, created_at, updated_at, session_id, importance FROM memory_entries`).
		Scan(&id, &et, &tags, &meta, &created, &updated, &sid, &imp); err != nil {
		t.Fatal(err)
	}
	if id != e.ID || !regexp.MustCompile(`^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$`).MatchString(id) {
		t.Fatalf("id %q is not str(uuid4())", id)
	}
	if !CLIValidEntryType(et) || et != "note" {
		t.Fatalf("entry_type %q", et)
	}
	if tags != `["health", "caf\u00e9"]` {
		t.Fatalf("tags %q, want Python json.dumps form", tags)
	}
	var tagList []string
	if err := json.Unmarshal([]byte(tags), &tagList); err != nil || tagList[1] != "café" {
		t.Fatalf("tags do not decode as list[str]: %v %v", tagList, err)
	}
	if meta != "{}" || sid.Valid || imp != 0.8 {
		t.Fatalf("meta=%q sid=%v imp=%v", meta, sid, imp)
	}
	iso := regexp.MustCompile(`^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?\+00:00$`)
	if !iso.MatchString(created) || !iso.MatchString(updated) {
		t.Fatalf("timestamps %q %q are not datetime.isoformat()", created, updated)
	}
	var fts int
	if err := s.db.QueryRow(`SELECT COUNT(*) FROM memory_fts WHERE memory_fts MATCH 'rules'`).Scan(&fts); err != nil || fts != 1 {
		t.Fatalf("FTS not in sync: %d %v", fts, err)
	}
	for _, table := range []string{"handoff_notes", "session_summaries", "user_profile", "user_facts", "user_traits", "chat_sessions", "chat_messages"} {
		if c, _ := s.columns(table); len(c) == 0 {
			t.Fatalf("schema lacks %s", table)
		}
	}
	if c, _ := s.columns("chat_sessions"); !c["llm_provider"] || !c["origin_channel"] {
		t.Fatal("migrations not applied")
	}
}

// A row exactly as Python wrote it reads back, and a Go runtime row that is
// not a MemoryEntry (reaction:<id>) is skipped rather than failing the list.
func TestCLIStoreReadsPythonRowsAndSkipsForeignOnes(t *testing.T) {
	s := cliStoreOpenT(t)
	if _, err := s.db.Exec(`INSERT INTO memory_entries (id, entry_type, title, content, tags, metadata, created_at, updated_at, session_id, importance) VALUES
		('f009ddc1-95d5-4d63-8bd1-1ccbe2508593', 'note', 'Python wrote this', 'über café', '["py", "\u00fc"]', '{"z": 1, "a": [1.0, "x"]}', '2026-11-27T00:07:04.176941+00:00', '2026-11-27T00:07:04.181403+00:00', 'pysess', 0.7),
		('reaction:msg1', 'reaction_feedback', 'Owner reacted', 'x', '["feedback"]', '{}', '2026-11-27T00:08:00Z', '2026-11-27T00:08:00Z', 's', 0.5)`); err != nil {
		t.Fatal(err)
	}
	got, err := s.ListRecent(10)
	if err != nil || len(got) != 1 {
		t.Fatalf("got %d entries, %v", len(got), err)
	}
	e := got[0]
	if e.Title != "Python wrote this" || e.Tags[1] != "ü" || *e.SessionID != "pysess" || e.CreatedISO() != "2026-11-27T00:07:04.176941+00:00" {
		t.Fatalf("%+v", e)
	}
	// Re-saving keeps metadata order and number types, as json.loads/dumps does.
	if err := s.Upsert(e); err != nil {
		t.Fatal(err)
	}
	var meta string
	_ = s.db.QueryRow(`SELECT metadata FROM memory_entries WHERE id = ?`, e.ID).Scan(&meta)
	if meta != `{"z": 1, "a": [1.0, "x"]}` {
		t.Fatalf("metadata rewritten as %q", meta)
	}
}

func TestCLIStoreFTS5Search(t *testing.T) {
	s := cliStoreOpenT(t)
	cliStoreAdd(t, s, "note", "Python errors", "Handling TypeErrors in async code", "", nil, 0.5)
	cliStoreAdd(t, s, "note", "Database tuning", "SQLite WAL mode improves perf", "", nil, 0.5)
	cliStoreAdd(t, s, "note", "API design", "REST vs GraphQL tradeoffs", "", nil, 0.5)
	hits, err := s.Search("Python", 20)
	if err != nil || len(hits) != 1 || hits[0].Title != "Python errors" {
		t.Fatalf("%v %v", hits, err)
	}
	if hits, _ = s.Search("SQLite OR async", 20); len(hits) < 1 {
		t.Fatal("FTS OR query found nothing")
	}
	// FTS syntax the user did not mean falls back to quoted tokens.
	if hits, err = s.Search("perf.", 20); err != nil || len(hits) != 1 {
		t.Fatalf("punctuated query: %v %v", hits, err)
	}
	// "vs:" reads as an FTS column filter: "no such column" skips the quoted
	// retry and goes straight to LIKE, which has no such substring (Python
	// answers the same: nothing).
	if hits, err = s.Search("vs: GraphQL", 20); err != nil || len(hits) != 0 {
		t.Fatalf("column-filter lookalike: %v %v", hits, err)
	}
	if hits, err = s.Search("vs GraphQL", 20); err != nil || len(hits) != 1 {
		t.Fatalf("plain words: %v %v", hits, err)
	}
	for _, bad := range []string{"", "  ", `"*"`, strings.Repeat("a", 501)} {
		var qe *CLISearchQueryError
		if _, err := s.Search(bad, 5); err == nil || !asCLIQueryErr(err, &qe) {
			t.Fatalf("query %q: want CLISearchQueryError, got %v", bad, err)
		}
	}
}

func asCLIQueryErr(err error, target **CLISearchQueryError) bool {
	qe, ok := err.(*CLISearchQueryError)
	*target = qe
	return ok
}

// Without an FTS table (a memory.db the Go runtime made before Python ever
// opened it) search still answers through LIKE.
func TestCLIStoreSearchFallsBackToLike(t *testing.T) {
	s := cliStoreOpenT(t)
	cliStoreAdd(t, s, "note", "Unique123", "Special content here", "", nil, 0.5)
	if _, err := s.db.Exec(`DROP TABLE memory_fts`); err != nil {
		t.Fatal(err)
	}
	hits, err := s.Search("Unique123", 20)
	if err != nil || len(hits) != 1 {
		t.Fatalf("%v %v", hits, err)
	}
}

func TestCLIStoreFTS5MatchQuery(t *testing.T) {
	for in, want := range map[string]string{
		"cats and dogs":    `"cats" "dogs"`,
		"a.b; c:d (e) NOT": `"a" "b" "c" "d" "e"`,
		"__x__ ü":          `"x" "ü"`,
		"...":              ``,
	} {
		if got := cliStoreFTS5MatchQuery(in); got != want {
			t.Fatalf("%q -> %q, want %q", in, got, want)
		}
	}
}

func TestCLIStoreHandoffCRUDAndMemoryMirror(t *testing.T) {
	s := cliStoreOpenT(t)
	h := NewCLIHandoffNote("Session Alpha to Beta", "Completed Phase 0. Moving to Phase 1.", nil)
	from := "alpha"
	h.FromSession = &from
	h.ActionItems = []string{"Implement FTS5 search", "Add handoff API"}
	if err := s.CreateHandoff(h); err != nil {
		t.Fatal(err)
	}
	if err := s.CreateHandoff(h); err != nil { // re-save: one memory row, not two
		t.Fatal(err)
	}
	got, err := s.GetHandoff(h.ID)
	if err != nil || got == nil || got.Title != h.Title || len(got.ActionItems) != 2 || got.Acknowledged {
		t.Fatalf("%+v %v", got, err)
	}
	mem, err := s.ListByType("handoff", 20)
	if err != nil || len(mem) != 1 {
		t.Fatalf("handoff memory rows %d %v", len(mem), err)
	}
	m := mem[0]
	if m.ID != h.ID || m.Title != "Handoff: "+h.Title || m.Importance != 0.9 || *m.SessionID != "alpha" {
		t.Fatalf("%+v", m)
	}
	var meta string
	_ = s.db.QueryRow(`SELECT metadata FROM memory_entries WHERE id = ?`, h.ID).Scan(&meta)
	want := `{"handoff_id": "` + h.ID + `", "from_session": "alpha", "action_items": ["Implement FTS5 search", "Add handoff API"], "decisions": []}`
	if meta != want {
		t.Fatalf("metadata %q\nwant     %q", meta, want)
	}
	if missing, err := s.GetHandoff("no-such-id"); missing != nil || err != nil {
		t.Fatalf("missing handoff: %v %v", missing, err)
	}
}

func TestCLIStoreListAndRelevantHandoffs(t *testing.T) {
	s := cliStoreOpenT(t)
	for _, n := range [][2]string{{"Phase 0 complete", "Scaffold done"}, {"Bug fix", "Fixed memory leak"}, {"100% done", "x"}} {
		if err := s.CreateHandoff(NewCLIHandoffNote(n[0], n[1], nil)); err != nil {
			t.Fatal(err)
		}
	}
	all, _ := s.ListHandoffs(50)
	if len(all) != 3 {
		t.Fatalf("%d", len(all))
	}
	hits, _ := s.RelevantHandoffs("Phase", 5)
	if len(hits) != 1 || !strings.Contains(hits[0].Title, "Phase") {
		t.Fatalf("%v", hits)
	}
	// LIKE wildcards in the query are literal.
	if hits, _ = s.RelevantHandoffs("%", 5); len(hits) != 1 || hits[0].Title != "100% done" {
		t.Fatalf("%% matched %d", len(hits))
	}
}

func TestCLIStorePendingHandoffs(t *testing.T) {
	s := cliStoreOpenT(t)
	if err := s.CreateHandoff(NewCLIHandoffNote("Pending", "Test", nil)); err != nil {
		t.Fatal(err)
	}
	acked := NewCLIHandoffNote("Done", "Test", nil)
	acked.Acknowledged = true
	_ = s.CreateHandoff(acked)
	p, err := s.PendingHandoffs()
	if err != nil || len(p) != 1 || p[0].Title != "Pending" {
		t.Fatalf("%v %v", p, err)
	}
}

func TestCLIStoreEndSessionWritesHandoffAndSummary(t *testing.T) {
	s := cliStoreOpenT(t)
	cliStoreAdd(t, s, "note", "Big decision", "x", "sess-1", nil, 0.8)
	cliStoreAdd(t, s, "note", "Other session", "x", "sess-2", nil, 0.9)
	h, err := s.EndSession("sess-1")
	if err != nil {
		t.Fatal(err)
	}
	if h.Title != "Handoff from session sess-1" || h.Content != "**Key memories**:\n- Big decision" ||
		strings.Join(h.Tags, ",") != "auto-handoff,session-sess-1" || *h.FromSession != "sess-1" {
		t.Fatalf("%+v", h)
	}
	var summary string
	if err := s.db.QueryRow(`SELECT summary FROM session_summaries WHERE session_id = 'sess-1'`).Scan(&summary); err != nil ||
		summary != "Session sess-1: 0 tasks, 0 skills created." {
		t.Fatalf("%q %v", summary, err)
	}
	h2, _ := s.EndSession("empty")
	if h2.Content != "Session ended." {
		t.Fatalf("%q", h2.Content)
	}
}

func TestCLIStoreConsolidateSession(t *testing.T) {
	s := cliStoreOpenT(t)
	var src []*CLIMemoryEntry
	for i := 0; i < 5; i++ {
		e := cliStoreAdd(t, s, "note", "Entry", "Content: working on project", "sess-1", []string{"project", "work"}, 0.6)
		src = append(src, e)
	}
	src[0].meta.set("action_items", []any{"ship it"})
	_ = s.Upsert(src[0])
	res, err := s.Consolidate("sess-1", 100)
	if err != nil || res == nil {
		t.Fatalf("%v %v", res, err)
	}
	if res.EntryType != "session" || !strings.Contains(res.Title, "sess-1") || res.Tags[0] != "project" || res.Importance != 0.75 {
		t.Fatalf("%+v", res)
	}
	if !strings.Contains(res.Content, "**Action items**:\n- ship it") {
		t.Fatalf("content %q", res.Content)
	}
	after, _ := s.listBySession("sess-1", 100)
	demoted := 0
	for _, e := range after {
		if v, _ := e.meta.get("demoted"); v == true && e.Importance == 0.25 {
			demoted++
		}
	}
	if demoted != 5 {
		t.Fatalf("demoted %d of 5", demoted)
	}
}

// Python's conditional expression swallows the whole summary when there are
// no action items; the port writes the same (empty) content.
func TestCLIStoreConsolidateWithoutActionItemsMatchesPython(t *testing.T) {
	s := cliStoreOpenT(t)
	for i := 0; i < 3; i++ {
		cliStoreAdd(t, s, "note", "Entry", "c", "sess-1", nil, 0.5)
	}
	res, err := s.Consolidate("sess-1", 100)
	if err != nil || res == nil || res.Content != "" {
		t.Fatalf("%+v %v", res, err)
	}
}

func TestCLIStoreConsolidateNotEnoughEntries(t *testing.T) {
	s := cliStoreOpenT(t)
	cliStoreAdd(t, s, "note", "a", "c", "sess-1", nil, 0.5)
	cliStoreAdd(t, s, "system", "b", "c", "sess-1", nil, 0.5)
	cliStoreAdd(t, s, "system", "c", "c", "sess-1", nil, 0.5)
	if res, err := s.Consolidate("sess-1", 100); res != nil || err != nil {
		t.Fatalf("%v %v", res, err)
	}
	if res, err := s.Consolidate("sess-empty", 100); res != nil || err != nil {
		t.Fatalf("%v %v", res, err)
	}
}

func cliStoreProfileFacts(t *testing.T, s *CLIStore) []map[string]any {
	t.Helper()
	p, err := s.loadOrCreateProfile("default")
	if err != nil {
		t.Fatal(err)
	}
	return cliStoreFactList(p)
}

func TestCLIStoreHiveDistillDoesNotWriteParent(t *testing.T) {
	s := cliStoreOpenT(t)
	if err := s.distill("Remember I prefer TypeScript for all new work", "hive_forager1"); err != nil {
		t.Fatal(err)
	}
	for _, f := range cliStoreProfileFacts(t, s) {
		if strings.Contains(asString(f["fact"]), "TypeScript") {
			t.Fatal("hive session wrote a parent fact")
		}
	}
}

func TestCLIStoreOwnerDistillWrites(t *testing.T) {
	s := cliStoreOpenT(t)
	if err := s.distill("Remember I prefer TypeScript for all new work", "sess-owner"); err != nil {
		t.Fatal(err)
	}
	found := false
	for _, f := range cliStoreProfileFacts(t, s) {
		if strings.Contains(asString(f["fact"]), "TypeScript") {
			found = true
			if f["authority"] != "owner" || f["inferred"] != false || f["source"] != "explicit" {
				t.Fatalf("explicit fact provenance %v", f)
			}
		}
	}
	if !found {
		t.Fatal("owner distill wrote nothing")
	}
	var n int
	_ = s.db.QueryRow(`SELECT COUNT(*) FROM user_facts WHERE fact LIKE '%TypeScript%'`).Scan(&n)
	if n == 0 {
		t.Fatal("user_facts not mirrored")
	}
	if notes, _ := s.ListByType("note", 10); len(notes) == 0 || notes[0].Importance != 0.85 {
		t.Fatal("explicit remember did not land a note")
	}
}

func TestCLIStoreDistillReinforcesAndNamesAndDecays(t *testing.T) {
	s := cliStoreOpenT(t)
	_ = s.distill("I prefer using git reflog for recovery.", "s1")
	_ = s.distill("I prefer using git reflog for recovery.", "s1")
	facts := cliStoreProfileFacts(t, s)
	if len(facts) != 1 || cliStoreInt(facts[0]["reference_count"], 0) != 2 {
		t.Fatalf("%v", facts)
	}
	// An old, rarely used fact loses a little confidence on the next pass.
	p, _ := s.loadOrCreateProfile("default")
	fl := cliStoreFactList(p)
	fl[0]["last_referenced"] = cliStoreISO(time.Now().Add(-120 * 24 * time.Hour))
	fl[0]["confidence"] = 0.8
	if err := s.saveProfile(p, "default"); err != nil {
		t.Fatal(err)
	}
	_ = s.distill("Hi, my name is Alex.", "s1")
	p, _ = s.loadOrCreateProfile("default")
	if p.vals["display_name"] != "Alex." { // Python keeps the full stop too
		t.Fatalf("display_name %v", p.vals["display_name"])
	}
	for _, f := range cliStoreFactList(p) {
		if strings.Contains(asString(f["fact"]), "reflog") {
			if c := cliStoreFloat(f["confidence"], 0); math.Abs(c-0.72) > 1e-9 {
				t.Fatalf("decayed to %v, want 0.72", c)
			}
		}
	}
}

func TestCLIStoreHeuristicExtraction(t *testing.T) {
	texts := func(fs []cliStoreFact) string {
		var b []string
		for _, f := range fs {
			b = append(b, strings.ToLower(f.text))
		}
		return strings.Join(b, " | ")
	}
	fs := cliStoreExtractHeuristicFacts("I prefer TypeScript for frontend. Please never force-push main.")
	if !strings.Contains(texts(fs), "typescript") {
		t.Fatalf("%v", fs)
	}
	high := false
	for _, f := range fs {
		high = high || f.confidence >= 0.85
	}
	if !high {
		t.Fatal("no high-confidence fact")
	}
	if fs := cliStoreExtractHeuristicFacts("always run the tests now"); len(fs) != 0 {
		t.Fatalf("noise stored: %v", fs)
	}
	ident := false
	for _, f := range cliStoreExtractHeuristicFacts("Hi, my name is Alex.") {
		ident = ident || f.category == "identity"
	}
	if !ident {
		t.Fatal("name not extracted")
	}
	if fs := cliStoreExtractHeuristicFacts("remember that password: hunter2 api_key=sk-abc1234567890"); len(fs) != 0 {
		t.Fatalf("secret stored: %v", fs)
	}
	if got := texts(cliStoreExtractHeuristicFacts("I have two kids. Don't mention my divorce. I prefer TypeScript.")); !strings.Contains(got, "typescript") ||
		!(strings.Contains(got, "kids") || strings.Contains(got, "divorce")) {
		t.Fatalf("merge: %s", got)
	}
}

func TestCLIStoreLivingExtraction(t *testing.T) {
	fs := cliStoreExtractLivingFacts("I have two kids. My goal this year is to ship Remedy 1.0. Be blunt.")
	cats := map[string]bool{}
	for _, f := range fs {
		cats[f.category] = true
	}
	if !cats["life"] || !cats["goal"] {
		t.Fatalf("%v", fs)
	}
	design := false
	for _, f := range cliStoreExtractLivingFacts("Too generic, too AI-looking. No rounded corners. Keep it brutalist.") {
		design = design || ((f.category == "correction" || f.category == "design") &&
			(strings.Contains(strings.ToLower(f.text), "rounded") || strings.Contains(strings.ToLower(f.text), "brutalist")))
	}
	if !design {
		t.Fatal("design taste not extracted")
	}
	for _, mood := range []string{"I'm tired today", "right now I have a headache"} {
		if fs := cliStoreExtractLivingFacts(mood); len(fs) != 0 {
			t.Fatalf("%q stored %v", mood, fs)
		}
	}
}

func TestCLIStoreProfileShapeMatchesPydantic(t *testing.T) {
	s := cliStoreOpenT(t)
	p, err := s.Profile()
	if err != nil || p.UserID != "default" || p.SessionsText != "0" || len(p.Facts) != 0 {
		t.Fatalf("%+v %v", p, err)
	}
	var raw string
	_ = s.db.QueryRow(`SELECT profile_json FROM user_profile WHERE user_id = 'default'`).Scan(&raw)
	order := []string{`"user_id": "default"`, `"display_name": null`, `"traits": {}`, `"facts": []`, `"stats": {`,
		`"sessions_count": 0`, `"total_interactions": 0`, `"skills_used": {}`, `"preferred_channels": []`,
		`"avg_session_duration_minutes": 0.0`, `"created_at": "`, `"last_active": "`}
	last := -1
	for _, k := range order {
		i := strings.Index(raw, k)
		if i < 0 || i < last {
			t.Fatalf("%q missing or out of model order in\n%s", k, raw)
		}
		last = i
	}
	if !regexp.MustCompile(`"created_at": "\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{6})?Z"`).MatchString(raw) {
		t.Fatalf("created_at not pydantic form:\n%s", raw)
	}
}

func TestCLIStoreSearchUserFacts(t *testing.T) {
	s := cliStoreOpenT(t)
	_ = s.distill("I prefer using git reflog for recovery. Never force-push main.", "s1")
	facts, err := s.SearchUserFacts("reflog", 10)
	if err != nil || len(facts) != 1 || facts[0].ReferenceCount != 1 {
		t.Fatalf("%v %v", facts, err)
	}
	if facts, _ = s.SearchUserFacts("", 10); len(facts) < 2 {
		t.Fatalf("empty query lists all: %v", facts)
	}
}

func TestCLIStoreIntegrityVacuumBackup(t *testing.T) {
	s := cliStoreOpenT(t)
	cliStoreAdd(t, s, "note", "Important", "Data", "", nil, 0.9)
	info, err := s.CheckIntegrity()
	if err != nil {
		t.Fatal(err)
	}
	keys := []string{"integrity", "quick_check", "page_count", "freelist_pages", "estimated_size_bytes", "entry_counts", "fte_count", "handoff_count", "session_count"}
	for i, k := range keys {
		if info[i].Key != k {
			t.Fatalf("key %d = %s, want %s", i, info[i].Key, k)
		}
	}
	if info[0].Text() != "ok" || info[5].Text() != "{'note': 1}" || info[6].Text() != "1" {
		t.Fatalf("%v", info)
	}
	if _, _, reclaimed, err := s.Vacuum(); err != nil || reclaimed < 0 {
		t.Fatalf("%d %v", reclaimed, err)
	}
	p, err := s.Backup()
	if err != nil || !strings.HasPrefix(filepath.Base(p), "memory_backup_") || filepath.Dir(p) != filepath.Join(filepath.Dir(s.Path()), "backups") {
		t.Fatalf("%s %v", p, err)
	}
	b, err := OpenCLIStore(p)
	if err != nil {
		t.Fatal(err)
	}
	defer b.Close()
	if got, _ := b.ListRecent(5); len(got) != 1 || got[0].Title != "Important" {
		t.Fatalf("backup lacks the entry: %v", got)
	}
}

func TestCLIStorePyJSON(t *testing.T) {
	for _, c := range []struct {
		in   string
		want string
	}{
		{`{"b":1,"a":[1.0,2.5e-7,1e16,"é\n"],"c":null,"d":true}`, `{"b": 1, "a": [1.0, 2.5e-07, 1e+16, "\u00e9\n"], "c": null, "d": true}`},
		{`[]`, `[]`},
		{`{}`, `{}`},
		{`["😀"]`, `["\ud83d\ude00"]`},
	} {
		v, err := cliStorePyLoads(c.in)
		if err != nil {
			t.Fatal(err)
		}
		if got := cliStorePyDumps(v, 0, true); got != c.want {
			t.Fatalf("%s -> %s, want %s", c.in, got, c.want)
		}
	}
	for f, want := range map[float64]string{0.5: "0.5", 1: "1.0", 1e-05: "1e-05", 123456789012345: "123456789012345.0", math.Nextafter(0.3, 1): "0.30000000000000004"} {
		if got := cliStorePyFloat(f); got != want {
			t.Fatalf("%v -> %s, want %s", f, got, want)
		}
	}
	v, _ := cliStorePyLoads(`{"name": "UTC", "it's": [1, true, null]}`)
	if got := cliStorePyRepr(v); got != `{'name': 'UTC', "it's": [1, True, None]}` {
		t.Fatalf("repr %s", got)
	}
}

func TestCLIStoreParseISO(t *testing.T) {
	for in, want := range map[string]string{
		"2026-01-02T03:04:05+00:00":        "2026-01-02T03:04:05+00:00",
		"2026-01-02T03:04:05.120Z":         "2026-01-02T03:04:05.120000+00:00",
		"2026-01-02 03:04:05.000001+05:30": "2026-01-02T03:04:05.000001+05:30",
		"2026-01-02T03:04:05":              "2026-01-02T03:04:05",
	} {
		ct, err := cliStoreParseISO(in)
		if err != nil || ct.iso(false) != want {
			t.Fatalf("%s -> %s %v, want %s", in, ct.iso(false), err, want)
		}
	}
	if _, err := cliStoreParseISO("yesterday"); err == nil {
		t.Fatal("accepted garbage")
	}
}
