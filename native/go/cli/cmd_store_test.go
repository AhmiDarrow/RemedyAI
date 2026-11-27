package cli

import (
	"bytes"
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
)

// Ports of tests/test_cli_store_commands.py (memory / user / session /
// handoff) plus the store bits of test_cli_harden.py and test_cli_dispatch.py.
// They run against a real memory.db in a temp home, because the thing worth
// checking is that a command and the store actually agree.

func storeRun(t *testing.T, home string, args ...string) (string, string, int) {
	t.Helper()
	t.Setenv("REMEDY_HOME", home) // Run publishes --home here; restore after
	var out, errb bytes.Buffer
	code := Run(append([]string{"--home", home}, args...), &out, &errb, strings.NewReader(""), Hooks{}, "test")
	return out.String(), errb.String(), code
}

func storeSeed(t *testing.T, home string, entries ...*httpapi.CLIMemoryEntry) {
	t.Helper()
	s, err := httpapi.OpenCLIStore(filepath.Join(home, "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	for _, e := range entries {
		if err := s.Upsert(e); err != nil {
			t.Fatal(err)
		}
	}
}

var storeUUIDRE = regexp.MustCompile(`[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}`)

func TestCLIStoreAddReportsID(t *testing.T) {
	home := t.TempDir()
	out, _, code := storeRun(t, home, "memory", "add", "The dentist is on Elm St", "Dr Rowe, 0400 111 222", "--tags", "health, contacts", "--importance", "0.8")
	if code != 0 || !strings.Contains(out, "Memory entry saved") || !storeUUIDRE.MatchString(out) {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreAddedEntryIsListed(t *testing.T) {
	home := t.TempDir()
	if _, _, code := storeRun(t, home, "memory", "add", "Bin night is Tuesday", "green bin"); code != 0 {
		t.Fatal(code)
	}
	out, _, code := storeRun(t, home, "memory", "list", "--limit", "10")
	if code != 0 || !strings.Contains(out, "Bin night") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreTagsSplitOnCommas(t *testing.T) {
	home := t.TempDir()
	if _, _, code := storeRun(t, home, "memory", "add", "Tagged", "x", "--tags", " health , contacts ,, "); code != 0 {
		t.Fatal(code)
	}
	s, err := httpapi.OpenCLIStore(filepath.Join(home, "memory.db"))
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	got, err := s.ListRecent(5)
	if err != nil || len(got) != 1 {
		t.Fatalf("%v %v", got, err)
	}
	if strings.Join(got[0].Tags, ",") != "health,contacts" {
		t.Fatalf("tags = %q", got[0].Tags)
	}
}

func TestCLIStoreListNarrowedToOneKind(t *testing.T) {
	home := t.TempDir()
	storeSeed(t, home,
		httpapi.NewCLIMemoryEntry("note", "a plain note", "body", nil, 0.5),
		httpapi.NewCLIMemoryEntry("user_fact", "a user fact", "body", nil, 0.5))
	out, _, code := storeRun(t, home, "memory", "list", "--type", "user_fact", "--limit", "10")
	if code != 0 || !strings.Contains(out, "a user fact") || strings.Contains(out, "a plain note") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreInvalidTypeExitsNonzero(t *testing.T) {
	home := t.TempDir()
	_, errOut, code := storeRun(t, home, "memory", "list", "--type", "bogus")
	if code != 1 || !strings.Contains(errOut, "not a valid MemoryEntryType") {
		t.Fatalf("code=%d err=%q", code, errOut)
	}
	_, errOut, code = storeRun(t, home, "memory", "add", "t", "c", "--type", "bogus")
	if code != 1 || !strings.Contains(errOut, "not a valid MemoryEntryType") {
		t.Fatalf("add: code=%d err=%q", code, errOut)
	}
	_, errOut, code = storeRun(t, home, "memory", "add", "t", "c", "--importance", "1.5")
	if code != 1 || !strings.Contains(errOut, "importance") {
		t.Fatalf("importance: code=%d err=%q", code, errOut)
	}
}

func TestCLIStoreLimitIsHonoured(t *testing.T) {
	home := t.TempDir()
	var es []*httpapi.CLIMemoryEntry
	for i := 0; i < 8; i++ {
		es = append(es, httpapi.NewCLIMemoryEntry("note", "entry number "+string(rune('a'+i)), "body", nil, 0.5))
	}
	storeSeed(t, home, es...)
	out, _, _ := storeRun(t, home, "memory", "list", "--limit", "2")
	if n := strings.Count(out, "entry number"); n != 2 {
		t.Fatalf("listed %d, want 2:\n%s", n, out)
	}
}

func TestCLIStoreSearchFindsWhatWasStored(t *testing.T) {
	home := t.TempDir()
	storeSeed(t, home, httpapi.NewCLIMemoryEntry("note", "The dentist is on Elm St", "Dr Rowe", nil, 0.5))
	out, _, code := storeRun(t, home, "memory", "search", "dentist")
	if code != 0 || !strings.Contains(out, "Elm St") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreSearchForNothingSaysNothing(t *testing.T) {
	home := t.TempDir()
	storeSeed(t, home, httpapi.NewCLIMemoryEntry("note", "The dentist is on Elm St", "body", nil, 0.5))
	out, _, code := storeRun(t, home, "memory", "search", "zzzz-no-such-thing")
	if code != 0 || strings.Contains(out, "Elm St") || !strings.Contains(out, "No entries found.") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreSearchRejectsUnusableQuery(t *testing.T) {
	home := t.TempDir()
	_, errOut, code := storeRun(t, home, "memory", "search", `"**"`)
	if code != 1 || !strings.Contains(errOut, "only invalid characters") {
		t.Fatalf("code=%d err=%q", code, errOut)
	}
}

func TestCLIStoreListingEmptyStoreIsNotAnError(t *testing.T) {
	out, _, code := storeRun(t, t.TempDir(), "memory", "list")
	if code != 0 || !strings.Contains(out, "No entries found.") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreRepairReportsIntegrity(t *testing.T) {
	home := t.TempDir()
	storeSeed(t, home, httpapi.NewCLIMemoryEntry("note", "something", "body", nil, 0.5))
	out, _, code := storeRun(t, home, "memory", "repair")
	if code != 0 || !strings.Contains(out, "Integrity") || !strings.Contains(out, "integrity: ok") ||
		!strings.Contains(out, "entry_counts: {'note': 1}") || strings.Contains(out, "reclaimed") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreVacuumSaysWhatItReclaimed(t *testing.T) {
	home := t.TempDir()
	storeSeed(t, home, httpapi.NewCLIMemoryEntry("note", "something", "body", nil, 0.5))
	out, _, code := storeRun(t, home, "memory", "repair", "--vacuum")
	if code != 0 || !regexp.MustCompile(`Vacuumed: reclaimed \d+ bytes`).MatchString(out) {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreBackupNamesTheFileItWrote(t *testing.T) {
	home := t.TempDir()
	storeSeed(t, home, httpapi.NewCLIMemoryEntry("note", "something", "body", nil, 0.5))
	out, _, code := storeRun(t, home, "memory", "backup")
	m := regexp.MustCompile(`Backup created: (.+memory_backup_\d{8}_\d{6}\.db)`).FindStringSubmatch(out)
	if code != 0 || m == nil {
		t.Fatalf("code=%d out=%q", code, out)
	}
	if filepath.Dir(m[1]) != filepath.Join(home, "backups") {
		t.Fatalf("backup in %s, want <home>/backups", filepath.Dir(m[1]))
	}
	if st, err := os.Stat(m[1]); err != nil || st.Size() == 0 {
		t.Fatalf("backup file: %v", err)
	}
	raw, err := os.ReadFile(strings.TrimSuffix(m[1], ".db") + ".json")
	if err != nil {
		t.Fatal(err)
	}
	var manifest map[string]any
	if err := json.Unmarshal(raw, &manifest); err != nil || manifest["backup_path"] != m[1] {
		t.Fatalf("manifest %v %v", manifest, err)
	}
}

func TestCLIStoreConsolidatingEmptySessionSaysSo(t *testing.T) {
	out, _, code := storeRun(t, t.TempDir(), "memory", "consolidate", "no-such-session", "--max-entries", "50")
	if code != 0 || !strings.Contains(out, "Not enough entries") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreConsolidatingASession(t *testing.T) {
	home := t.TempDir()
	var es []*httpapi.CLIMemoryEntry
	for i := 0; i < 3; i++ {
		e := httpapi.NewCLIMemoryEntry("note", "Entry", "working on project", []string{"project"}, 0.6)
		sid := "sess-1"
		e.SessionID = &sid
		es = append(es, e)
	}
	storeSeed(t, home, es...)
	out, _, code := storeRun(t, home, "memory", "consolidate", "sess-1")
	if code != 0 || !strings.Contains(out, "Consolidated session sess-1:") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreShowingAMissingProfileCreatesOne(t *testing.T) {
	home := t.TempDir()
	out, _, code := storeRun(t, home, "user", "show")
	if code != 0 || !strings.Contains(out, "User Profile") || !strings.Contains(out, "User: default") || !strings.Contains(out, "Facts (0):") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreSearchingFactsOnEmptyProfileSaysNone(t *testing.T) {
	out, _, code := storeRun(t, t.TempDir(), "user", "facts", "anything", "--limit", "10")
	if code != 0 || !strings.Contains(out, "No facts found") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreCreatingAHandoffReportsItsID(t *testing.T) {
	out, _, code := storeRun(t, t.TempDir(), "handoff", "create", "Where we got to", "the parser works", "--tags", "build")
	if code != 0 || !strings.Contains(out, "Handoff created") || !storeUUIDRE.MatchString(out) {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreCreatedHandoffIsListedSearchedAndShown(t *testing.T) {
	home := t.TempDir()
	out, _, _ := storeRun(t, home, "handoff", "create", "Parser rewrite", "left it half done", "--tags", "build")
	id := storeUUIDRE.FindString(out)
	if out, _, _ = storeRun(t, home, "handoff", "list", "--limit", "10"); !strings.Contains(out, "Parser rewrite") || !strings.Contains(out, "pending") {
		t.Fatalf("list %q", out)
	}
	if out, _, _ = storeRun(t, home, "handoff", "search", "parser", "--limit", "10"); !strings.Contains(out, "Parser rewrite") {
		t.Fatalf("search %q", out)
	}
	out, _, code := storeRun(t, home, "handoff", "show", id)
	var got map[string]any
	if code != 0 || json.Unmarshal([]byte(out), &got) != nil {
		t.Fatalf("show code=%d out=%q", code, out)
	}
	keys := []string{"id", "title", "content", "tags", "from_session", "to_session", "context_summary", "action_items", "decisions", "created_at", "acknowledged"}
	if len(got) != len(keys) {
		t.Fatalf("keys %v", got)
	}
	last := -1
	for _, k := range keys {
		i := strings.Index(out, `"`+k+`":`)
		if i < 0 || i < last {
			t.Fatalf("key %q missing or out of HandoffNote order in %s", k, out)
		}
		last = i
	}
	if got["id"] != id || got["acknowledged"] != false || !strings.HasSuffix(got["created_at"].(string), "Z") {
		t.Fatalf("show %v", got)
	}
}

func TestCLIStoreShowingAMissingHandoffExitsNonzero(t *testing.T) {
	_, errOut, code := storeRun(t, t.TempDir(), "handoff", "show", "no-such-id")
	if code != 1 || !strings.Contains(strings.ToLower(errOut), "not found") {
		t.Fatalf("code=%d err=%q", code, errOut)
	}
}

func TestCLIStoreSessionStartPersistsMarker(t *testing.T) {
	home := t.TempDir()
	out, _, code := storeRun(t, home, "session", "start")
	if code != 0 || !strings.Contains(out, "Session started") {
		t.Fatalf("code=%d out=%q", code, out)
	}
	raw, err := os.ReadFile(filepath.Join(home, "cli_active_session"))
	if err != nil || strings.TrimSpace(string(raw)) == "" || !strings.Contains(out, strings.TrimSpace(string(raw))) {
		t.Fatalf("marker %q %v", raw, err)
	}
}

func TestCLIStoreSessionEndWithoutStartIsQuiet(t *testing.T) {
	out, _, code := storeRun(t, t.TempDir(), "session", "end")
	if code != 0 || !strings.Contains(out, "No active session") {
		t.Fatalf("code=%d out=%q", code, out)
	}
}

func TestCLIStoreSessionEndAfterStartCreatesHandoff(t *testing.T) {
	home := t.TempDir()
	storeRun(t, home, "session", "start")
	out, _, code := storeRun(t, home, "session", "end")
	if code != 0 || !strings.Contains(out, "Handoff created") || !strings.Contains(out, "Session ended.") {
		t.Fatalf("code=%d out=%q", code, out)
	}
	if _, err := os.Stat(filepath.Join(home, "cli_active_session")); !os.IsNotExist(err) {
		t.Fatalf("marker still there: %v", err)
	}
	// The next start lists the auto-handoff as pending.
	out, _, _ = storeRun(t, home, "session", "start")
	if !strings.Contains(out, "1 pending handoff(s) from previous sessions:") || !strings.Contains(out, "Handoff from session") {
		t.Fatalf("second start %q", out)
	}
}

func TestCLIStoreDatabaseComesFromHome(t *testing.T) {
	home := t.TempDir()
	storeRun(t, home, "memory", "list")
	if _, err := os.Stat(filepath.Join(home, "memory.db")); err != nil {
		t.Fatalf("memory.db not under --home: %v", err)
	}
}

func TestCLIStoreGroupsNeedASubcommand(t *testing.T) {
	for _, g := range []string{"memory", "user", "session", "handoff"} {
		if _, _, code := storeRun(t, t.TempDir(), g); code != 2 {
			t.Fatalf("remedy %s exited %d, want 2", g, code)
		}
	}
}
