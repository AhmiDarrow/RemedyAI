package prompt

import (
	"strings"
	"testing"
)

// She talks in the chat while she works unless the owner chose otherwise.
func TestWorkUpdatesShapeWhatSheSaysWhileWorking(t *testing.T) {
	cases := map[string]string{
		"":      "never run a batch of tool calls without one",
		"steps": "never run a batch of tool calls without one",
		"bogus": "never run a batch of tool calls without one",
		"key":   "when something fails, and when you finish",
		" END ": "work quietly",
	}
	for setting, want := range cases {
		got := buildSystemPrompt(identityFromConfig(map[string]any{"work_updates": setting}), true)
		if !strings.Contains(got, want) {
			t.Errorf("work_updates=%q: the prompt does not say %q", setting, want)
		}
		if !strings.Contains(got, "the person does not see it in the chat") {
			t.Errorf("work_updates=%q: thinking must be named as private", setting)
		}
	}
}
