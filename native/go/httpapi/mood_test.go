package httpapi

import (
	"testing"
	"time"
)

func TestMoodIsOneRuleFromWhatSheIsDoing(t *testing.T) {
	s := &Server{}
	if got := s.currentMood(); got != "idle" {
		t.Fatalf("nothing happening: %s", got)
	}
	// A turn: thinking, then tools, then the third step.
	s.moodNote("a", func(p *moodPhase) { p.thinking = true })
	if got := s.currentMood(); got != "thinking" {
		t.Fatalf("thinking: %s", got)
	}
	s.moodNote("a", func(p *moodPhase) { p.tools++; p.steps++; p.thinking = false })
	if got := s.currentMood(); got != "working" {
		t.Fatalf("first tool: %s", got)
	}
	s.moodNote("a", func(p *moodPhase) { p.steps += 2 })
	if got := s.currentMood(); got != "focused" {
		t.Fatalf("third step with a tool running: %s", got)
	}
	// Between tools she is still working, not idle.
	s.moodNote("a", func(p *moodPhase) { p.tools = 0; p.ok = 3 })
	if got := s.currentMood(); got != "working" {
		t.Fatalf("between tools: %s", got)
	}
	// It ends well: proud for a while, then resting.
	s.moodEnd("a")
	if got := s.currentMood(); got != "proud" {
		t.Fatalf("after three good steps: %s", got)
	}
	s.mood.lastAt = time.Now().Add(-moodAfterglow - time.Minute)
	if got := s.currentMood(); got != "idle" {
		t.Fatalf("later: %s", got)
	}
	// A turn where some steps failed, and one where all did.
	s.moodNote("b", func(p *moodPhase) { p.steps, p.ok, p.err = 2, 1, 1 })
	s.moodEnd("b")
	if got := s.currentMood(); got != "concerned" {
		t.Fatalf("some failed: %s", got)
	}
	s.moodNote("c", func(p *moodPhase) { p.steps, p.err = 1, 1 })
	s.moodEnd("c")
	if got := s.currentMood(); got != "error" {
		t.Fatalf("all failed: %s", got)
	}
	// Her own work shows when no turn is running, and a turn comes first.
	s.mood.lastAt = time.Time{}
	s.innerAct.Store("learning")
	if got := s.currentMood(); got != "dreaming" {
		t.Fatalf("learning: %s", got)
	}
	s.moodNote("d", func(*moodPhase) {})
	if got := s.currentMood(); got != "working" {
		t.Fatalf("a turn beats her own work: %s", got)
	}
}
