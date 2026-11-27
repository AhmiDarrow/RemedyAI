package drives

import (
	"encoding/json"
	"math"
	"os"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

func TestInferOutcome(t *testing.T) {
	cases := []struct {
		text string
		ok   []bool
		want string
	}{
		{"", nil, ""},
		{"   ", []bool{true, true}, ""},
		{"Thanks, that worked perfectly.", nil, OutcomeAccepted},
		{"thank you!", nil, OutcomeAccepted},
		{"Yes do that", nil, OutcomeAccepted},
		{"yes, do it", nil, OutcomeAccepted},
		{"nice, thanks", nil, OutcomeAccepted},
		{"That's wrong, still broken.", nil, OutcomeUnhappy},
		{"it does not work", nil, OutcomeUnhappy},
		{"not what I asked", nil, OutcomeUnhappy},
		{"undo that please", nil, OutcomeUnhappy},
		// Unhappy wins over accepted in one message.
		{"thanks but it's still broken", nil, OutcomeUnhappy},
		// Fix held needs two or more steps, all ok.
		{"looks good, ship it", []bool{true, true}, OutcomeFixHeld},
		{"still works", []bool{true, true, true}, OutcomeFixHeld},
		{"looks good", []bool{true}, ""},
		{"looks good", []bool{true, false}, ""},
		{"looks good", nil, ""},
		// Word boundaries: "worse" inside a word does not count.
		{"show me the worsening trend", nil, ""},
		{"run the tests", []bool{true, true}, ""},
	}
	for _, c := range cases {
		if got := InferOutcome(c.text, c.ok); got != c.want {
			t.Errorf("InferOutcome(%q, %v) = %q, want %q", c.text, c.ok, got, c.want)
		}
	}
}

// test_outcomes_feed_state (tests/test_alive_organism.py).
func TestOutcomesFeedState(t *testing.T) {
	clk := &clock{time.Unix(1_750_000_000, 0)}
	env := soul.Env{Home: t.TempDir(), Clock: clk.now}
	before, _ := Load(env)
	res, err := NoteTurnOutcome(env, "Thanks, that worked perfectly.", nil)
	if err != nil || res.Kind != OutcomeAccepted {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	mid, _ := Load(env)
	if mid.Affect.Valence < before.Affect.Valence {
		t.Fatalf("accepted lowered valence: %v -> %v", before.Affect.Valence, mid.Affect.Valence)
	}
	if got := mid.Outcomes[len(mid.Outcomes)-1]["kind"]; got != OutcomeAccepted {
		t.Fatalf("last outcome=%v", got)
	}
	if ts, _ := mid.Outcomes[0]["ts"].(float64); ts != 1_750_000_000 {
		t.Fatalf("outcome ts=%v", mid.Outcomes[0]["ts"])
	}
	res, err = NoteTurnOutcome(env, "That's wrong, still broken.", nil)
	if err != nil || res.Kind != OutcomeUnhappy {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	after, _ := Load(env)
	if after.Affect.Valence >= mid.Affect.Valence {
		t.Fatalf("unhappy did not lower valence: %v -> %v", mid.Affect.Valence, after.Affect.Valence)
	}
}

func TestNoteTurnOutcomeNudges(t *testing.T) {
	env := soul.Env{Home: t.TempDir(), Clock: (&clock{time.Unix(1_750_000_000, 0)}).now}
	if _, err := NoteTurnOutcome(env, "perfect", nil); err != nil {
		t.Fatal(err)
	}
	st, _ := Load(env)
	near := func(a, b float64) bool { return math.Abs(a-b) < 1e-9 }
	if !near(st.Affect.Valence, 0.61) || !near(st.Drives["competence"], 0.5) || !near(st.Drives["social"], 0.43) {
		t.Fatalf("accepted nudges: affect=%+v drives=%v", st.Affect, st.Drives)
	}
	if st.Affect.Label != "steady" {
		t.Fatalf("label=%q", st.Affect.Label)
	}
	if _, err := NoteTurnOutcome(env, "stop that", nil); err != nil {
		t.Fatal(err)
	}
	st, _ = Load(env)
	if !near(st.Affect.Valence, 0.53) || !near(st.Affect.Arousal, 0.45) ||
		!near(st.Drives["competence"], 0.44) || !near(st.Drives["curiosity"], 0.48) {
		t.Fatalf("unhappy nudges: affect=%+v drives=%v", st.Affect, st.Drives)
	}
}

func TestNoteTurnOutcomeNothingInferredWritesNothing(t *testing.T) {
	env := soul.Env{Home: t.TempDir()}
	res, err := NoteTurnOutcome(env, "run the tests", []bool{true, true})
	if err != nil || res.Kind != "" || res.Skipped != "" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	if _, err := os.Stat(AlivePath(env.Home)); !os.IsNotExist(err) {
		t.Fatalf("alive.json written with no outcome: %v", err)
	}
}

func TestNoteTurnOutcomeKnobOff(t *testing.T) {
	env := soul.Env{Home: t.TempDir(), Knobs: map[string]any{"outcome_learning": false}}
	res, err := NoteTurnOutcome(env, "thanks", nil)
	if err != nil || res.Skipped != "off" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	if _, err := os.Stat(AlivePath(env.Home)); !os.IsNotExist(err) {
		t.Fatal("outcome_learning off must not write alive.json")
	}
}

func TestNoteTurnOutcomeCapsAndKeepsFields(t *testing.T) {
	env := soul.Env{Home: t.TempDir()}
	var rows []map[string]any
	for i := 0; i < outcomeCap; i++ {
		rows = append(rows, map[string]any{"ts": float64(i), "kind": "unhappy"})
	}
	seed, _ := json.Marshal(map[string]any{"schema": 1, "outcomes": rows, "future_field": "kept",
		"affect": map[string]any{"valence": 0.5, "arousal": 0.4, "label": "steady", "tint": "blue"}})
	if err := os.WriteFile(AlivePath(env.Home), seed, 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := NoteTurnOutcome(env, "that fixed it", nil); err != nil {
		t.Fatal(err)
	}
	st, _ := Load(env)
	if len(st.Outcomes) != outcomeCap || st.Outcomes[0]["ts"] != float64(1) || st.Outcomes[outcomeCap-1]["kind"] != OutcomeAccepted {
		t.Fatalf("outcomes not capped to the newest %d: first=%v last=%v n=%d", outcomeCap,
			st.Outcomes[0], st.Outcomes[len(st.Outcomes)-1], len(st.Outcomes))
	}
	raw, _ := os.ReadFile(AlivePath(env.Home))
	if !strings.Contains(string(raw), `"future_field": "kept"`) || !strings.Contains(string(raw), `"tint": "blue"`) {
		t.Fatalf("unknown fields dropped: %s", raw)
	}
}

func TestNoteTurnOutcomeLeavesUnreadableFile(t *testing.T) {
	env := soul.Env{Home: t.TempDir()}
	if err := os.WriteFile(AlivePath(env.Home), []byte("{not json"), 0o600); err != nil {
		t.Fatal(err)
	}
	if _, err := NoteTurnOutcome(env, "thanks", nil); err == nil {
		t.Fatal("an unreadable alive.json must be an error")
	}
	raw, _ := os.ReadFile(AlivePath(env.Home))
	if string(raw) != "{not json" {
		t.Fatalf("an unreadable alive.json was overwritten: %s", raw)
	}
}

// Outcomes are what the dream reads to shape her affect.
func TestOutcomesShapeDreamAffect(t *testing.T) {
	happy := soul.Env{Home: t.TempDir()}
	sad := soul.Env{Home: t.TempDir()}
	for i := 0; i < 4; i++ {
		_, _ = NoteTurnOutcome(happy, "thanks", nil)
		_, _ = NoteTurnOutcome(sad, "still broken", nil)
	}
	h, _ := Load(happy)
	s, _ := Load(sad)
	if recentOutcomeValence(&h) <= recentOutcomeValence(&s) {
		t.Fatalf("outcome valence happy=%v sad=%v", recentOutcomeValence(&h), recentOutcomeValence(&s))
	}
}

func TestOrganismTickWithSkillsRefined(t *testing.T) {
	clk := &clock{time.Unix(1_750_000_000, 0)}
	a := soul.Env{Home: t.TempDir(), Clock: clk.now}
	b := soul.Env{Home: t.TempDir(), Clock: clk.now}
	ra, err := OrganismTickWith(a, 2)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := OrganismTick(b); err != nil {
		t.Fatal(err)
	}
	sa, _ := Load(a)
	sb, _ := Load(b)
	if sa.Drives["competence"] <= sb.Drives["competence"] || sa.Drives["rest"] <= sb.Drives["rest"] {
		t.Fatalf("refined skills must count as heavy work: %v vs %v", sa.Drives, sb.Drives)
	}
	if ra.ChangedMap()["skills_refined"] != 2 || ra.Changed {
		t.Fatalf("report=%+v changed=%v", ra, ra.ChangedMap())
	}
}
