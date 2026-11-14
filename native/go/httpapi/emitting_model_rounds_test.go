package httpapi

import (
	"context"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

type roundsModel struct{ rounds [][]string }

func (r *roundsModel) ContextWindow() int { return 8192 }

func (r *roundsModel) Stream(context.Context, cognition.Turn) (<-chan cognition.ModelEvent, error) {
	texts := r.rounds[0]
	r.rounds = r.rounds[1:]
	ch := make(chan cognition.ModelEvent, len(texts)+1)
	for _, t := range texts {
		ch <- cognition.ModelEvent{Text: t}
	}
	ch <- cognition.ModelEvent{Done: true}
	close(ch)
	return ch, nil
}

func streamRounds(t *testing.T, rounds ...[]string) string {
	t.Helper()
	var got strings.Builder
	m := &emittingModel{inner: &roundsModel{rounds: rounds}, emit: func(tok string) {
		if text, ok := modelTextToken(tok); ok {
			got.WriteString(text)
		}
	}}
	for range rounds {
		ch, err := m.Stream(context.Background(), cognition.Turn{})
		if err != nil {
			t.Fatal(err)
		}
		for range ch {
		}
	}
	return got.String()
}

// Narration from one model round ("…work.") and the next round's text used to
// run together as "…work.The project tools…".
func TestEmittingModelSeparatesTextBetweenRounds(t *testing.T) {
	got := streamRounds(t,
		[]string{"I'll reach the portal folder ", "through the shell."},
		[]string{"Cmd is eating the search patterns."},
		[]string{"Done: the addon is updated."},
	)
	want := "I'll reach the portal folder through the shell.\n\n" +
		"Cmd is eating the search patterns.\n\n" +
		"Done: the addon is updated."
	if got != want {
		t.Fatalf("got %q\nwant %q", got, want)
	}
}

func TestEmittingModelKeepsDeltasWithinARoundJoined(t *testing.T) {
	if got := streamRounds(t, []string{"Hel", "lo ", "there."}); got != "Hello there." {
		t.Fatalf("got %q", got)
	}
}

func TestEmittingModelDoesNotDoubleExistingBreaks(t *testing.T) {
	got := streamRounds(t, []string{"First.\n"}, []string{"\nSecond."}, []string{"Third.\n\n"}, []string{"Fourth."})
	if got != "First.\n\nSecond.\n\nThird.\n\nFourth." {
		t.Fatalf("got %q", got)
	}
}

func TestEmittingModelSkipsSeparatorAfterTextlessRound(t *testing.T) {
	// A tool-only round (no text) in between still gets one paragraph break.
	got := streamRounds(t, []string{"Looking."}, []string{}, []string{"Found it."})
	if got != "Looking.\n\nFound it." {
		t.Fatalf("got %q", got)
	}
}
