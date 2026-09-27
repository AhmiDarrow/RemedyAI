package cognition

import (
	"context"
	"fmt"
	"strings"
	"sync/atomic"
	"testing"
)

func TestFailureNoteLeavesARedTestAlone(t *testing.T) {
	st := failNoteState{}
	red := ToolResult{
		Name:    "bash",
		IsError: true,
		Output:  []byte("FAILED tests/test_app.py::test_verbose_flag\nAssertionError: expected 1"),
	}
	if note := nextFailureNote([]ToolResult{red}, &st); note != "" {
		t.Fatalf("a red test is the model's to fix: %s", note)
	}
	if note := nextFailureNote([]ToolResult{red}, &st); note != "" {
		t.Fatalf("a second red test is still the model's: %s", note)
	}
}

func TestFailureNoteSearchesAMissingModuleOnce(t *testing.T) {
	st := failNoteState{}
	missing := ToolResult{
		Name:   "bash",
		Output: []byte(`{"exit_code":1,"stderr":"ModuleNotFoundError: No module named 'foo'"}`),
	}
	note := nextFailureNote([]ToolResult{missing}, &st)
	if !strings.Contains(note, "web.search") {
		t.Fatalf("missing module: %q", note)
	}
	if strings.Contains(note, "stop") {
		t.Fatalf("the lookup must not tell the model to stop: %s", note)
	}
	if again := nextFailureNote([]ToolResult{missing}, &st); again != "" {
		t.Fatalf("the lookup is once, then the model decides: %q", again)
	}
}

func TestFailureNoteIgnoresAReadThatMentionsTheError(t *testing.T) {
	st := failNoteState{}
	note := nextFailureNote([]ToolResult{{
		Name:   "read",
		Output: []byte("docs say: command not found"),
	}}, &st)
	if note != "" {
		t.Fatalf("a read is not a check: %q", note)
	}
}

func TestFailureNoteIgnoresAMissingProjectFile(t *testing.T) {
	st := failNoteState{}
	note := nextFailureNote([]ToolResult{{
		Name:    "bash",
		IsError: true,
		Output:  []byte("No such file or directory: src/app.py"),
	}}, &st)
	if note != "" {
		t.Fatalf("a missing project file is not an outside fact: %q", note)
	}
}

func TestEngineAppendsTheFailureNote(t *testing.T) {
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n > 2 {
				return events(ModelEvent{Text: "stopping", Done: true}), nil
			}
			return events(ModelEvent{ToolCall: &ToolCall{
				ID:    fmt.Sprintf("c%d", n),
				Name:  "bash",
				Input: []byte(fmt.Sprintf(`{"command":"pytest %d"}`, n)),
			}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{
				ID:      c.ID,
				Name:    c.Name,
				IsError: true,
				Output:  []byte(`{"exit_code":1,"stderr":"ModuleNotFoundError: No module named 'foo'"}`),
			}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 10, SoftEpochSteps: -1, MaxRepeatedBatch: 20},
	}
	out := engine.Run(context.Background(), "build the app")
	if out.Err != nil {
		t.Fatalf("turn: %v", out.Err)
	}
	text := transcriptText(out.Messages)
	if !strings.Contains(text, "web.search") {
		t.Fatalf("missing search note:\n%s", text)
	}
	if strings.Contains(text, "Do not run") {
		t.Fatalf("the note must not stop the model:\n%s", text)
	}
}
