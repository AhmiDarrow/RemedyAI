package dream

import (
	"os"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

const tomato = "remind me tomorrow to water the tomato plants"

type clock struct{ t time.Time }

func (c *clock) now() time.Time { return c.t }

func seeded(t *testing.T, episodes int, user string) (soul.Env, *clock) {
	t.Helper()
	c := &clock{time.Unix(1_750_000_000, 0)}
	env := soul.Env{Home: t.TempDir(), Clock: c.now}
	for i := 0; i < episodes; i++ {
		if _, err := soul.UpdateAfterTurn(env, soul.Turn{UserText: user, AssistantText: "ok"}); err != nil {
			t.Fatal(err)
		}
	}
	return env, c
}

func TestDreamTable(t *testing.T) {
	cases := []struct {
		name      string
		episodes  int
		advance   time.Duration
		skipped   bool
		reason    string
		firstFree bool
		user      string
	}{
		{"dreams on enough episodes", 4, 0, false, "", true, tomato},
		{"too few episodes skips", 1, 0, true, "too_few_episodes", true, "hello there"},
		{"second pass inside cooldown skips", 4, 10 * time.Second, true, "cooldown", false, tomato},
	}
	for _, c := range cases {
		t.Run(c.name, func(t *testing.T) {
			env, clk := seeded(t, c.episodes, c.user)
			if !c.firstFree {
				if _, err := Cycle(env, false); err != nil {
					t.Fatal(err)
				}
				clk.t = clk.t.Add(c.advance)
			}
			before, _ := os.ReadFile(soul.FieldPath(env.Home))
			res, err := Cycle(env, false)
			if err != nil {
				t.Fatal(err)
			}
			if res.Skipped != c.skipped || res.Reason != c.reason {
				t.Fatalf("res=%+v", res)
			}
			if c.skipped {
				after, _ := os.ReadFile(soul.FieldPath(env.Home))
				if string(after) != string(before) || res.Field != nil || res.PromotedThreads+res.HabitsAdded+res.FutureDreams != 0 {
					t.Fatal("a skipped dream must change nothing and report nothing")
				}
				return
			}
			if res.PromotedThreads != 0 && res.Top() == "" {
				t.Fatalf("res=%+v", res)
			}
			if len(res.Dreams) == 0 || !strings.HasPrefix(res.Top(), "Toward ") {
				t.Fatalf("dream must compose a future line: %+v", res)
			}
		})
	}
}

func TestCooldownSurvivesRestart(t *testing.T) {
	env, clk := seeded(t, 4, tomato)
	if res, err := Cycle(env, false); err != nil || res.Skipped {
		t.Fatalf("first dream: %+v %v", res, err)
	}
	// A "restart": nothing in memory; a fresh Env on the same home.
	again := soul.Env{Home: env.Home, Clock: clk.now}
	if due, _ := ShouldDream(again); due {
		t.Fatal("the cooldown must be read from disk after a restart")
	}
	clk.t = clk.t.Add(121 * time.Second)
	if due, _ := ShouldDream(again); !due {
		t.Fatal("the cooldown must end")
	}
}
