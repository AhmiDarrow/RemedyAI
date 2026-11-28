package soul

import (
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"testing"
	"time"
)

func testEnv(t *testing.T) (Env, *time.Time) {
	t.Helper()
	now := time.Unix(1_750_000_000, 0)
	return Env{Home: t.TempDir(), Clock: func() time.Time { return now }}, &now
}

// A field.json as the Python writes it, with fields Go does not know.
const pythonField = `{
  "schema": 1,
  "identity_name": "Remedy",
  "identity_gender": "female",
  "identity_vow": "I stay.",
  "self_habits": ["Do the work they want, and keep them posted in the chat as it goes."],
  "relational": {"rapport": 0.61, "trust": 0.58, "correction_style": "blunt", "voice_markers": ["ship it"],
    "speech_register": "casual", "help_mode": "pair", "open_threads": ["check in about the garden tomorrow"],
    "tensions": [], "turns_together": 7, "last_user_ts": 1749990000.0, "last_valence": 0.3,
    "partner_name": "Sam"},
  "episodes": [{"id": "a1", "ts": 1749990000.0, "arc": "user: plan the garden", "user_stance": "exploratory",
    "open_thread": "", "valence": 0.25, "muscle": "xai/grok", "session_id": "s1", "project_hint": "",
    "strength": 0.0, "recalls": 0, "last_recall_ts": 0.0, "mood_tag": "sunny"}],
  "organism_lessons": [],
  "pledges": ["from now on we test first"],
  "pledge_traces": {"from now on we test first": {"strength": 0.5, "recalls": 1, "last_recall_ts": 1749990000.0, "ts": 1749980000.0}, "bad": 3},
  "future_dreams": [],
  "updated_ts": 1749990000.0,
  "somatic": {"pulse": 2}
}`

func TestPythonFieldRoundTripKeepsUnknownFields(t *testing.T) {
	env, _ := testEnv(t)
	path := FieldPath(env.Home)
	if err := os.MkdirAll(filepath.Dir(path), 0o700); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(path, []byte(pythonField), 0o600); err != nil {
		t.Fatal(err)
	}
	f, err := UpdateAfterTurn(env, Turn{UserText: "thanks, that garden plan is great", AssistantText: "Glad it helps.", Provider: "xai", Model: "grok"})
	if err != nil {
		t.Fatal(err)
	}
	if f.Relational.TurnsTogether != 8 || len(f.Episodes) != 2 {
		t.Fatalf("turns=%d episodes=%d", f.Relational.TurnsTogether, len(f.Episodes))
	}
	if f.Episodes[0].Strength == 0 {
		t.Fatal("a pre-strength episode must be backfilled")
	}
	raw, _ := os.ReadFile(path)
	var got map[string]any
	if err := json.Unmarshal(raw, &got); err != nil {
		t.Fatal(err)
	}
	if got["somatic"] == nil {
		t.Fatal("unknown top-level field lost")
	}
	if rel := got["relational"].(map[string]any); rel["partner_name"] != "Sam" {
		t.Fatalf("unknown relational field lost: %v", rel)
	}
	if ep := got["episodes"].([]any)[0].(map[string]any); ep["mood_tag"] != "sunny" {
		t.Fatalf("unknown episode field lost: %v", ep)
	}
	if tr := got["pledge_traces"].(map[string]any); tr["bad"] != nil || tr["from now on we test first"] == nil {
		t.Fatalf("pledge traces must keep dict entries only: %v", tr)
	}
	if got["identity_vow"] != "I stay." {
		t.Fatalf("vow=%v", got["identity_vow"])
	}
	if _, err := os.Stat(filepath.Join(env.Home, Dir, ProprioFile)); err != nil {
		t.Fatal("proprioception.json must be written after a turn")
	}
}

func TestUpdateAfterTurnTable(t *testing.T) {
	cases := []struct {
		name, session, user string
		turns, episodes     int
	}{
		{"plain turn", "s1", "let's fix the login form together", 1, 1},
		{"hive daughter writes nothing", "hive_x", "hello there", 0, 0},
		{"secret only counts", "s1", "my api_key = sk-abcdefghijklmnop", 1, 0},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			env, _ := testEnv(t)
			if _, err := UpdateAfterTurn(env, Turn{SessionID: c.session, UserText: c.user, AssistantText: "Done."}); err != nil {
				t.Fatal(err)
			}
			f, err := Load(env)
			if err != nil {
				t.Fatal(err)
			}
			if f.Relational.TurnsTogether != c.turns || len(f.Episodes) != c.episodes {
				t.Fatalf("turns=%d episodes=%d", f.Relational.TurnsTogether, len(f.Episodes))
			}
			raw, _ := os.ReadFile(FieldPath(env.Home))
			if strings.Contains(string(raw), "sk-abcdef") {
				t.Fatal("a secret reached the soul")
			}
		})
	}
}

func TestPledgeAndHelpMode(t *testing.T) {
	env, _ := testEnv(t)
	for i := 0; i < 2; i++ {
		if _, err := UpdateAfterTurn(env, Turn{UserText: "From now on we write the test before the code.", AssistantText: "ok"}); err != nil {
			t.Fatal(err)
		}
	}
	f, _ := Load(env)
	if len(f.Pledges) != 1 {
		t.Fatalf("pledges=%v", f.Pledges)
	}
	tr := f.PledgeTraces[f.Pledges[0]].(map[string]any)
	if tr["recalls"].(float64) != 1 {
		t.Fatalf("a re-stated pledge must reconsolidate: %v", tr)
	}
}

func TestCorruptFieldIsNeverOverwritten(t *testing.T) {
	env, _ := testEnv(t)
	path := FieldPath(env.Home)
	_ = os.MkdirAll(filepath.Dir(path), 0o700)
	_ = os.WriteFile(path, []byte("{not json"), 0o600)
	if _, err := UpdateAfterTurn(env, Turn{UserText: "hi"}); err != ErrPersistBlocked {
		t.Fatalf("err=%v, want ErrPersistBlocked", err)
	}
	if raw, _ := os.ReadFile(path); string(raw) != "{not json" {
		t.Fatal("corrupt field was overwritten")
	}
}

func TestRetainKeepsRecentAndSalient(t *testing.T) {
	var eps []Episode
	for i := 0; i < 20; i++ {
		eps = append(eps, Episode{ID: string(rune('a' + i)), TS: 1000, Strength: 0.1})
	}
	eps[2].Strength, eps[2].Valence = 1, 1
	out := RetainEpisodes(eps, 1000, 6, nil)
	// 4 newest by position, then the 2 strongest older ones: c (intense), then a (first of the ties).
	if len(out) != 6 || out[0].ID != "a" || out[1].ID != "c" || out[2].ID != "q" || out[5].ID != "t" {
		t.Fatalf("kept %v", out)
	}
}

func TestResetKeepsIdentityOnly(t *testing.T) {
	env, _ := testEnv(t)
	_, _ = Mutate(env, func(f *Field) (bool, error) {
		f.IdentityName, f.IdentityGender = "Ava", "neutral"
		f.Pledges = []string{"we promise to rest on sundays"}
		f.Relational.TurnsTogether = 9
		return true, nil
	})
	if err := Reset(env); err != nil {
		t.Fatal(err)
	}
	f, _ := Load(env)
	if f.IdentityName != "Ava" || f.IdentityGender != "neutral" || len(f.Pledges) != 0 || f.Relational.TurnsTogether != 0 {
		t.Fatalf("after reset: %+v", f)
	}
}

func TestDriftDetection(t *testing.T) {
	got := DetectDrift("As an AI language model I don't have memory. Sorry, sorry, my apologies.", "")
	if len(got) < 2 || got[0] != "identity_reset" || got[1] != "over_apology" {
		t.Fatalf("drift=%v", got)
	}
	if len(DetectDrift("I live to be useful.", "what is your creed: live to be useful?")) != 0 {
		t.Fatal("a prompted creed is not drift")
	}
}

func TestWorkResidue(t *testing.T) {
	for _, s := range []string{`continue: C:\work\x`, "user: hi | did: ran tests", "see /home/me/x"} {
		if !LooksLikeWorkResidue(s) {
			t.Fatalf("%q must be residue", s)
		}
	}
	if LooksLikeWorkResidue("check in about the garden tomorrow") {
		t.Fatal("a relational thread is not residue")
	}
}
