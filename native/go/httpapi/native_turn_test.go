package httpapi

import (
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func TestTurnHistoryWindowMovesInSteps(t *testing.T) {
	// The first carried message is total-window; it must stay put for a run
	// of turns, then jump, and the window never drops below the minimum.
	prevStart, moves := -1, 0
	const upTo = 3 * turnHistoryMessages
	for total := 0; total <= upTo; total++ {
		w := turnHistoryWindow(total)
		if total >= turnHistoryMessages && (w < turnHistoryMessages || w >= turnHistoryMessages+turnHistoryBlock) {
			t.Fatalf("total %d: window %d outside [%d, %d)", total, w, turnHistoryMessages, turnHistoryMessages+turnHistoryBlock)
		}
		start := total - w
		if start < 0 {
			start = 0
		}
		if start < prevStart {
			t.Fatalf("total %d: window start went backwards (%d after %d)", total, start, prevStart)
		}
		if start != prevStart {
			moves++
		}
		prevStart = start
	}
	if moves > upTo/turnHistoryBlock+1 {
		t.Fatalf("the window start moved %d times over %d messages; it should move about every %d", moves, upTo, turnHistoryBlock)
	}
}

func TestTurnContextGoesJustBeforeTheOwnersMessage(t *testing.T) {
	msgs := []cognition.Message{
		cognition.UserText("earlier question"),
		{Role: cognition.RoleAssistant, Blocks: []cognition.Block{cognition.TextBlock("earlier answer")}},
		cognition.UserText("new question"),
	}
	out := withTurnContext(msgs, "[Turn context]\nmood")
	if len(out) != 4 {
		t.Fatalf("want 4 messages, got %d", len(out))
	}
	if out[0].Blocks[0].Text != "earlier question" || out[1].Blocks[0].Text != "earlier answer" {
		t.Fatal("earlier turns must be untouched, so their bytes match last turn's request")
	}
	if !out[2].Internal || out[2].Role != cognition.RoleUser || out[2].Blocks[0].Text != "[Turn context]\nmood" {
		t.Fatalf("the note is an internal user message before the goal: %+v", out[2])
	}
	if out[3].Blocks[0].Text != "new question" || out[3].Internal {
		t.Fatalf("the owner's message stays last: %+v", out[3])
	}
	if got := withTurnContext(msgs, "  "); len(got) != 3 {
		t.Fatal("no context means no note")
	}
}
