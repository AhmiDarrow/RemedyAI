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

func TestIsEasyUserChatIsConservative(t *testing.T) {
	if !isEasyUserChat("hi", "", false) {
		t.Fatal("greeting should be easy")
	}
	if !isEasyUserChat("thanks!", "", false) {
		t.Fatal("thanks should be easy")
	}
	if !isEasyUserChat("ok", "brief_update", false) {
		t.Fatal("inner brief should be easy")
	}
	if isEasyUserChat("What's the best way to structure a multi-tenant SaaS billing system?", "", false) {
		t.Fatal("open-ended design question must not be easy")
	}
	if isEasyUserChat("Explain gravity in simple terms", "", false) {
		t.Fatal("explain-prompts stay on the selected provider")
	}
	if isEasyUserChat("def foo():\n    pass\nPlease refactor this module.", "coding", false) {
		t.Fatal("coding must not be easy")
	}
	if isEasyUserChat("hi", "", true) {
		t.Fatal("attachments are not easy chats")
	}
}

func TestMuscleRouteTargetAutoAndForced(t *testing.T) {
	// args: policy, prompt, kind, sessionProv, cfgProv, hasMind, hasRMB, hasLocal, hasFrontier, activeCloud, mindHandlesEasy
	if got := muscleRouteTarget("auto", "hi", "brief_update", "", "rmb", true, true, false, true, false, true); got != "mind" {
		t.Fatalf("easy local work should stay on the mind, got %q", got)
	}
	mid := "def foo():\n    pass\nPlease refactor this module."
	if got := muscleRouteTarget("auto", mid, "coding", "", "rmb", true, true, false, true, false, true); got != "rmb" {
		t.Fatalf("mid coding should use RMB, got %q", got)
	}
	hard := "Please implement a multi-file architecture refactor and formally prove the new algorithm. " + stringsRepeat("code\n", 400)
	if got := muscleRouteTarget("auto", hard, "coding", "", "rmb", true, true, false, true, false, true); got != "frontier" {
		t.Fatalf("hardest work should escalate, got %q", got)
	}
	if got := muscleRouteTarget("auto", hard, "coding", "", "rmb", true, true, false, false, false, true); got != "rmb" {
		t.Fatalf("hard work without a frontier stays local, got %q", got)
	}
	if got := muscleRouteTarget("auto", "hi", "", "openai", "openai", true, true, false, true, true, true); got != "mind" {
		t.Fatalf("easy cloud chats should divert to the 4B mind, got %q", got)
	}
	if got := muscleRouteTarget("auto", mid, "coding", "openai", "openai", true, true, false, true, true, true); got != "" {
		t.Fatalf("complex cloud chats must stay on the selected provider, got %q", got)
	}
	if got := muscleRouteTarget("auto", "What's the best way to design a billing system?", "", "openai", "openai", true, false, false, true, true, true); got != "" {
		t.Fatalf("open-ended cloud questions must stay on the selected provider, got %q", got)
	}
	if got := muscleRouteTarget("auto", "hi", "", "openai", "openai", true, true, false, true, true, false); got != "" {
		t.Fatalf("mind_handles_easy_chats=false must leave cloud sessions alone, got %q", got)
	}
	if got := muscleRouteTarget("mind", "anything", "", "openai", "openai", true, true, false, true, true, true); got != "mind" {
		t.Fatalf("forced mind should win, got %q", got)
	}
}
