package drives

import (
	"encoding/json"
	"math"
	"os"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

type clock struct{ t time.Time }

func (c *clock) now() time.Time { return c.t }

func TestDriftFollowsElapsedTime(t *testing.T) {
	cases := []struct {
		name    string
		elapsed time.Duration
	}{{"20s", 20 * time.Second}, {"60s", time.Minute}, {"15m", 15 * time.Minute}}
	drop := map[string]float64{}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			clk := &clock{time.Unix(1_750_000_000, 0)}
			env := soul.Env{Home: t.TempDir(), Clock: clk.now}
			if _, err := Tick(env, TickInput{}); err != nil { // first tick sets the clock
				t.Fatal(err)
			}
			st, _ := Load(env)
			rest := st.Drives["rest"]
			clk.t = clk.t.Add(c.elapsed)
			if _, err := Tick(env, TickInput{}); err != nil {
				t.Fatal(err)
			}
			st, _ = Load(env)
			drop[c.name] = rest - st.Drives["rest"]
		})
	}
	// One tick after 15 minutes must move as far as fifteen 1-minute ticks
	// (about 45x a 20 s tick), not the same as any single tick.
	if drop["20s"] <= 0 || drop["15m"] < 30*drop["20s"] {
		t.Fatalf("drift is not time-based: %v", drop)
	}
	if math.Abs(drop["60s"]*3-drop["20s"]*9) > 0.002 {
		t.Fatalf("drift is not linear in time over short spans: %v", drop)
	}
}

func TestAliveKeepsUnknownFields(t *testing.T) {
	clk := &clock{time.Unix(1_750_000_000, 0)}
	env := soul.Env{Home: t.TempDir(), Clock: clk.now}
	raw := `{"schema":1,"drives":{"curiosity":0.9,"social":0.4,"competence":0.4,"rest":0.2},
	"affect":{"valence":0.6,"arousal":0.4,"label":"steady","hue":"blue"},"autobiography":"",
	"beliefs":["About us: we plant tomatoes"],"outcomes":[{"ts":1,"kind":"accepted"}],"pending_changes":[],
	"reach_out":{"last_ts":0,"pending":false,"reason":"","gloss":""},"last_tick_ts":0,"last_reflection_ts":0,
	"last_idle":"","updated_ts":0,"future_field":[1,2]}`
	if err := os.WriteFile(AlivePath(env.Home), []byte(raw), 0o600); err != nil {
		t.Fatal(err)
	}
	res, err := Tick(env, TickInput{})
	if err != nil || res.Idle != "dream" {
		t.Fatalf("res=%+v err=%v", res, err)
	}
	data, _ := os.ReadFile(AlivePath(env.Home))
	var got map[string]any
	_ = json.Unmarshal(data, &got)
	if got["future_field"] == nil || got["affect"].(map[string]any)["hue"] != "blue" {
		t.Fatalf("unknown fields lost: %s", data)
	}
	if len(got["pending_changes"].([]any)) != 1 {
		t.Fatal("first tick queues the weekly reflection")
	}
}

func TestOrganismTickCooldownIsNotAChange(t *testing.T) {
	clk := &clock{time.Unix(1_750_000_000, 0)}
	env := soul.Env{Home: t.TempDir(), Clock: clk.now}
	for i := 0; i < 4; i++ {
		if _, err := soul.UpdateAfterTurn(env, soul.Turn{UserText: "remind me tomorrow to water the tomato plants", AssistantText: "ok"}); err != nil {
			t.Fatal(err)
		}
	}
	first, err := OrganismTick(env)
	if err != nil || !first.Dreamed || !first.Changed {
		t.Fatalf("first=%+v err=%v", first, err)
	}
	clk.t = clk.t.Add(20 * time.Second)
	second, err := OrganismTick(env)
	if err != nil {
		t.Fatal(err)
	}
	if second.Dreamed || second.Changed {
		t.Fatalf("a tick inside the dream cooldown must not count as changed: %+v", second)
	}
	st, _ := Load(env)
	if st.Autobiography == "" {
		t.Fatal("a dream must write the autobiography")
	}
}

func TestDrivesOffIsANoOp(t *testing.T) {
	env := soul.Env{Home: t.TempDir(), Knobs: map[string]any{"drives_enabled": false}}
	res, err := Tick(env, TickInput{})
	if err != nil || res.Skipped != "drives_off" {
		t.Fatalf("res=%+v", res)
	}
	if _, err := os.Stat(AlivePath(env.Home)); err == nil {
		t.Fatal("drives off must not write alive.json")
	}
}
