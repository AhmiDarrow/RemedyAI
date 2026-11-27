package drives

import (
	"regexp"
	"strings"

	sf "github.com/AhmiDarrow/RemedyAI/native/go/mind/internal/statefile"
	"github.com/AhmiDarrow/RemedyAI/native/go/mind/soul"
)

// Outcome kinds a turn can leave in alive.json "outcomes".
const (
	OutcomeAccepted = "accepted"
	OutcomeUnhappy  = "unhappy"
	OutcomeFixHeld  = "fix_held"
)

// The same phrases as alive.py _UNHAPPY / _ACCEPTED / the fix-held check.
var (
	unhappyRE = regexp.MustCompile(`(?i)\b(still broken|doesn't work|does not work|that's wrong|thats wrong|` +
		`not what i (wanted|asked)|worse|stop that|undo that)\b`)
	acceptedRE = regexp.MustCompile(`(?i)\b(thanks|thank you|that worked|that fixed it|perfect|exactly|` +
		`yes,? (do|please) (that|it)|nice,?\s+(thanks|that))\b`)
	fixHeldRE = regexp.MustCompile(`(?i)\b(still works|holding|looks good|ship it)\b`)
)

// InferOutcome is alive.py _infer_outcome: a cheap read of the owner's
// message. stepsOK holds one entry per tool step of the turn (true when it
// succeeded). It returns "" when the message says nothing either way.
func InferOutcome(userText string, stepsOK []bool) string {
	text := strings.TrimSpace(userText)
	if text == "" {
		return ""
	}
	if unhappyRE.MatchString(text) {
		return OutcomeUnhappy
	}
	if acceptedRE.MatchString(text) {
		return OutcomeAccepted
	}
	// A later turn that continues after tools all succeeded: a weak "fix held".
	if len(stepsOK) >= 2 {
		all := true
		for _, ok := range stepsOK {
			all = all && ok
		}
		if all && fixHeldRE.MatchString(text) {
			return OutcomeFixHeld
		}
	}
	return ""
}

// NoteResult reports NoteTurnOutcome.
type NoteResult struct {
	Kind    string `json:"kind,omitempty"`
	Skipped string `json:"skipped,omitempty"`
}

// NoteTurnOutcome is alive.py note_turn_outcome: infer the outcome of the
// turn from the owner's message, append it to alive.json "outcomes" (capped)
// and nudge affect and drives. Off when the outcome_learning knob is off.
// alive.json is written only when an outcome was inferred.
func NoteTurnOutcome(env soul.Env, userText string, stepsOK []bool) (NoteResult, error) {
	if !sf.Bool(env.Knobs, "outcome_learning", true) {
		return NoteResult{Skipped: "off"}, nil
	}
	kind := InferOutcome(userText, stepsOK)
	if kind == "" {
		return NoteResult{}, nil
	}
	_, err := mutate(env, func(st *State) (bool, error) {
		st.Outcomes = append(st.Outcomes, map[string]any{"ts": sf.Unix(env.Now()), "kind": kind})
		if len(st.Outcomes) > outcomeCap {
			st.Outcomes = st.Outcomes[len(st.Outcomes)-outcomeCap:]
		}
		a := &st.Affect
		switch kind {
		case OutcomeAccepted, OutcomeFixHeld:
			a.Valence = clamp01(a.Valence + 0.06)
			nudge(st.Drives, "competence", 0.05)
			nudge(st.Drives, "social", -0.02)
		case OutcomeUnhappy:
			a.Valence = clamp01(a.Valence - 0.08)
			a.Arousal = clamp01(a.Arousal + 0.05)
			nudge(st.Drives, "competence", -0.06)
			nudge(st.Drives, "curiosity", 0.03)
		}
		a.Label = label(a.Valence, a.Arousal)
		return true, nil
	})
	if err != nil {
		return NoteResult{}, err
	}
	return NoteResult{Kind: kind}, nil
}
