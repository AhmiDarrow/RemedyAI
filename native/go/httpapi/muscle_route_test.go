package httpapi

import "testing"

func TestEstimateDifficultyMatchesPythonBands(t *testing.T) {
	if got := estimateDifficulty("ok", "continuity_core"); got >= 0.3 {
		t.Fatalf("inner work should stay easy, got %v", got)
	}
	mid := estimateDifficulty("def foo():\n    pass\nPlease refactor this module.", "coding")
	if mid < 0.4 || mid >= 0.85 {
		t.Fatalf("short refactor should be mid-band, got %v", mid)
	}
	hard := estimateDifficulty(
		"Please implement a multi-file architecture refactor and formally prove the new algorithm. "+stringsRepeat("code\n", 400),
		"coding",
	)
	if hard < 0.85 {
		t.Fatalf("hardest work should reach the frontier band, got %v", hard)
	}
}

func stringsRepeat(s string, n int) string {
	out := make([]byte, 0, len(s)*n)
	for i := 0; i < n; i++ {
		out = append(out, s...)
	}
	return string(out)
}

func TestMuscleRouteTargetAutoAndForced(t *testing.T) {
	if got := muscleRouteTarget("auto", "hi", "brief_update", "", "rmb", true, true, false, true, false); got != "mind" {
		t.Fatalf("easy local work should stay on the mind, got %q", got)
	}
	mid := "def foo():\n    pass\nPlease refactor this module."
	if got := muscleRouteTarget("auto", mid, "coding", "", "rmb", true, true, false, true, false); got != "rmb" {
		t.Fatalf("mid coding should use RMB, got %q", got)
	}
	hard := "Please implement a multi-file architecture refactor and formally prove the new algorithm. " + stringsRepeat("code\n", 400)
	if got := muscleRouteTarget("auto", hard, "coding", "", "rmb", true, true, false, true, false); got != "frontier" {
		t.Fatalf("hardest work should escalate, got %q", got)
	}
	if got := muscleRouteTarget("auto", hard, "coding", "", "rmb", true, true, false, false, false); got != "rmb" {
		t.Fatalf("hard work without a frontier stays local, got %q", got)
	}
	if got := muscleRouteTarget("auto", mid, "coding", "openai", "openai", true, true, false, true, true); got != "" {
		t.Fatalf("auto must not steal a cloud session, got %q", got)
	}
	if got := muscleRouteTarget("mind", "anything", "", "openai", "openai", true, true, false, true, true); got != "mind" {
		t.Fatalf("forced mind should win, got %q", got)
	}
}
