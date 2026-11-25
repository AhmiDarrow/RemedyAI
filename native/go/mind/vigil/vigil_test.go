package vigil

import (
	"encoding/json"
	"fmt"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/drives"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

type clock struct {
	mu sync.Mutex
	t  time.Time
}

func (c *clock) now() time.Time { c.mu.Lock(); defer c.mu.Unlock(); return c.t }
func (c *clock) add(d time.Duration) {
	c.mu.Lock()
	c.t = c.t.Add(d)
	c.mu.Unlock()
}

func newEnv(t *testing.T) (soul.Env, *clock) {
	t.Helper()
	c := &clock{t: time.Date(2026, 11, 20, 1, 0, 0, 0, time.UTC)}
	return soul.Env{Home: t.TempDir(), Clock: c.now}, c
}

func turns(t *testing.T, env soul.Env, n int) {
	t.Helper()
	for i := 0; i < n; i++ {
		if _, err := soul.UpdateAfterTurn(env, soul.Turn{UserText: "remind me tomorrow to water the tomato plants", AssistantText: "Will do."}); err != nil {
			t.Fatal(err)
		}
	}
}

// vigil.json as the Python writes it (no total_dreams yet), plus a field Go does not know.
const pythonVigil = `{"schema": 1, "enabled": true, "max_wakes_per_day": 2, "min_gap_s": 3600, "day": "",
 "wakes_today": 0, "last_wake_ts": 0.0, "last_act": "", "total_wakes": 5, "offered": true,
 "tended": {"old thread": 1.0}, "custom_note": "keep me"}`

func TestPythonVigilRoundTripAndSeededDreams(t *testing.T) {
	env, _ := newEnv(t)
	_ = os.MkdirAll(filepath.Join(env.Home, soul.Dir), 0o700)
	_ = os.WriteFile(env.Path(StateFile), []byte(pythonVigil), 0o600)
	journal := `{"ts":1,"act":"dream","detail":"a","ok":true}` + "\n" + `{"ts":2,"act":"dream","detail":"b","ok":false}` + "\n"
	_ = os.WriteFile(env.Path(JournalFile), []byte(journal), 0o600)
	st, err := SetEnabled(env, false)
	if err != nil {
		t.Fatal(err)
	}
	if st.TotalWakes != 5 || st.TotalDreams != 1 || st.MaxWakesPerDay != 2 {
		t.Fatalf("state=%+v", st)
	}
	raw, _ := os.ReadFile(env.Path(StateFile))
	var got map[string]any
	_ = json.Unmarshal(raw, &got)
	if got["custom_note"] != "keep me" || got["enabled"] != false || got["total_dreams"] != 1.0 {
		t.Fatalf("vigil.json=%s", raw)
	}
}

func TestWakeLimits(t *testing.T) {
	cases := []struct {
		name    string
		steps   []time.Duration // advance before each tick
		results []string        // "woke" or a skip reason
	}{
		{"min gap", []time.Duration{0, 10 * time.Minute, 50 * time.Minute}, []string{"woke", "too_soon", "woke"}},
		{"daily cap", []time.Duration{0, 2 * time.Hour, 2 * time.Hour}, []string{"woke", "woke", "budget_spent"}},
		{"new day resets the cap", []time.Duration{0, 2 * time.Hour, 24 * time.Hour}, []string{"woke", "woke", "woke"}},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			env, clk := newEnv(t)
			turns(t, env, 4)
			_ = os.WriteFile(env.Path(StateFile), []byte(`{"enabled":true,"max_wakes_per_day":2,"min_gap_s":3600}`), 0o600)
			for i, d := range c.steps {
				clk.add(d)
				clk.add(3 * time.Minute) // the dream cooldown is 2 minutes
				res, err := Tick(env)
				if err != nil {
					t.Fatal(err)
				}
				got := res.Skipped
				if res.Woke {
					got = "woke"
				}
				if got != c.results[i] {
					t.Fatalf("tick %d = %+v, want %s", i, res, c.results[i])
				}
			}
		})
	}
}

func TestDisabledAndRest(t *testing.T) {
	env, _ := newEnv(t)
	if res, _ := Tick(env); res.Skipped != "disabled" {
		t.Fatalf("res=%+v", res)
	}
	if _, err := SetEnabled(env, true); err != nil {
		t.Fatal(err)
	}
	res, err := Tick(env)
	if err != nil || !res.Rested {
		t.Fatalf("nothing due must rest: %+v %v", res, err)
	}
	st, _ := Load(env)
	if st.WakesToday != 0 || st.TotalWakes != 0 || st.LastWakeTS != 0 {
		t.Fatalf("rest must cost nothing: %+v", st)
	}
}

func TestDreamActJournalsWhatItConcluded(t *testing.T) {
	env, _ := newEnv(t)
	turns(t, env, 4)
	if _, err := SetEnabled(env, true); err != nil {
		t.Fatal(err)
	}
	res, err := Tick(env)
	if err != nil || res.Act != ActDream || !res.OK {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	entries, _ := ReadJournal(env)
	if len(entries) != 1 || !strings.HasPrefix(entries[0].Detail, "Toward ") || entries[0].Added["dreams"] < 1 {
		t.Fatalf("journal=%+v", entries)
	}
	st, _ := Load(env)
	if st.TotalWakes != 1 || st.TotalDreams != 1 {
		t.Fatalf("totals=%+v", st)
	}
}

func TestJournalRingUnderConcurrentAppends(t *testing.T) {
	env, _ := newEnv(t)
	var wg sync.WaitGroup
	for g := 0; g < 8; g++ {
		wg.Add(1)
		go func(g int) {
			defer wg.Done()
			for i := 0; i < 100; i++ {
				if err := appendJournal(env, Entry{TS: float64(g*1000 + i + 1), Act: "tend", Detail: fmt.Sprint(g, i), OK: true}); err != nil {
					t.Error(err)
					return
				}
			}
		}(g)
	}
	wg.Wait()
	raw, _ := os.ReadFile(env.Path(JournalFile))
	lines := strings.Split(strings.TrimRight(string(raw), "\n"), "\n")
	// 800 appends trim at 401 lines twice: 200 + 198 remain.
	if len(lines) != 398 {
		t.Fatalf("lines=%d", len(lines))
	}
	if len(lines) > journalMaxLines || len(lines) < journalKeepLines {
		t.Fatalf("ring holds %d lines", len(lines))
	}
	for _, l := range lines {
		var e Entry
		if json.Unmarshal([]byte(l), &e) != nil {
			t.Fatalf("torn line %q", l)
		}
	}
}

// A turn, a night tick and a learn tick with no Python anywhere.
func TestEndToEndWithoutPython(t *testing.T) {
	env, clk := newEnv(t)
	turns(t, env, 4)
	if _, err := SetEnabled(env, true); err != nil {
		t.Fatal(err)
	}
	night, err := Tick(env)
	if err != nil || !night.Woke {
		t.Fatalf("night=%+v err=%v", night, err)
	}
	clk.add(5 * time.Minute)
	turns(t, env, 1)
	learn, err := drives.OrganismTick(env)
	if err != nil || !learn.Dreamed {
		t.Fatalf("learn=%+v err=%v", learn, err)
	}
	for _, p := range []string{soul.FieldPath(env.Home), env.Path(StateFile), env.Path(JournalFile), env.Path(soul.ProprioFile), drives.AlivePath(env.Home)} {
		if fi, err := os.Stat(p); err != nil || fi.Size() == 0 {
			t.Fatalf("%s missing", p)
		}
	}
	entries, _ := ReadJournal(env)
	if len(entries) != 1 || entries[0].Act != ActDream || !entries[0].OK {
		t.Fatalf("journal=%+v", entries)
	}
}
