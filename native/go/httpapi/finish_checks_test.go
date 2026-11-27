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
