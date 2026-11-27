package httpapi

import (
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func TestFinishChecksPushOnlySmallModelsByDefault(t *testing.T) {
	large, small := 200_000, cognition.SmallContextWindow
	cases := []struct {
		mode, reason string
		window       int
		want         bool
	}{
		// Auto: a large model is sent back only when a check failed.
		{"", "verify_failed", large, true},
		{"", "wants_tools", large, false},
		{"", "unverified", large, false},
		{"", "agency_promise", 0, false}, // a window nobody reported is not a small one
		// Auto: a small model keeps every check.
		{"", "wants_tools", small, true},
		{"auto", "unverified", 8192, true},
		// The owner's word either way.
		{"all", "wants_tools", large, true},
		{"off", "verify_failed", small, false},
	}
	for _, c := range cases {
		cfg := ConfigMap{}
		if c.mode != "" {
			cfg["finish_checks"] = c.mode
		}
		if got := finishCheckApplies(cfg, c.reason, c.window); got != c.want {
			t.Errorf("finish_checks=%q reason=%s window=%d: got %v, want %v", c.mode, c.reason, c.window, got, c.want)
		}
	}
}

func TestHarnessFiguresSurviveARestart(t *testing.T) {
	home := t.TempDir()
	sid := "sess-keep"
	out := cognition.Outcome{}
	out.Context.Window, out.Context.Budget, out.Context.Estimate = 200_000, 168_000, 4_200
	out.Context.ModelCalls = 3
	out.Context.Steps = []cognition.ToolStep{{Name: "bash", OK: true}, {Name: "read", OK: false}}
	recordHarness(home, sid, &out)
	// A restart: nothing in memory, the same home.
	harness.Lock()
	harness.bySession = map[string]*sessionHarness{}
	harness.latest = ""
	harness.Unlock()
	setHarnessHome(home)
	got := harnessFor(sid)
	if got.Turns != 1 || got.Estimate != 4_200 || got.Steps != 2 || got.StepsOK != 1 || got.ModelCalls != 3 {
		t.Fatalf("saved figures were not read back: %+v", got)
	}
	// With no session named, the one that ran last.
	if last := harnessFor(""); last.Turns != 1 {
		t.Fatalf("the latest session's figures were not found: %+v", last)
	}
}
