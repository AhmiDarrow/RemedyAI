package httpapi

import (
	"context"
	"strings"
	"testing"
	"time"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

func TestCognitionTurnRunnerEmitsTextAndCompletes(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{Text: "Hello ", Done: false}, {Text: "world", Done: true}},
	}}
	r := NewCognitionTurnRunner(model)
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "hi"})
	if err != nil {
		t.Fatal(err)
	}
	if out != "Hello world" {
		t.Fatalf("got %q", out)
	}
}

func TestCognitionTurnRunnerEmitsToolCallThenResultWhenAllowed(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "1", Name: "file_read", Input: []byte(`{"path":"a.py"}`)}}},
		{{Text: "done", Done: true}},
	}}
	r := &CognitionTurnRunner{
		Model:  model,
		Tools:  cognition.EchoTools{},
		Policy: cognition.AllowAll{},
	}
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "read"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, `@@tool_call:`) || !strings.Contains(out, `"file_read"`) {
		t.Fatalf("missing tool_call: %q", out)
	}
	if !strings.Contains(out, `@@tool_result:`) || !strings.Contains(out, `"ok":true`) {
		t.Fatalf("missing tool_result: %q", out)
	}
	if !strings.Contains(out, "done") {
		t.Fatalf("missing final text: %q", out)
	}
}

func TestCognitionTurnRunnerAbortEmitsControlToken(t *testing.T) {
	model := cognition.Model(cognitionModelFunc(func(ctx context.Context, _ cognition.Turn) (<-chan cognition.ModelEvent, error) {
		ch := make(chan cognition.ModelEvent)
		go func() {
			defer close(ch)
			select {
			case <-ctx.Done():
				return
			case <-time.After(2 * time.Second):
				ch <- cognition.ModelEvent{Text: "late", Done: true}
			}
		}()
		return ch, nil
	}))
	r := NewCognitionTurnRunner(model)
	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(20 * time.Millisecond)
		cancel()
	}()
	out, err := CollectTokens(ctx, r, TurnRequest{Prompt: "x"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "@@aborted") {
		t.Fatalf("expected @@aborted, got %q", out)
	}
}

func TestCognitionTurnRunnerOwnerCheckpointStatus(t *testing.T) {
	model := &cognition.ScriptedModel{Rounds: [][]cognition.ModelEvent{
		{{ToolCall: &cognition.ToolCall{ID: "pay", Name: "payment.submit", Input: []byte(`{}`)}}},
	}}
	r := &CognitionTurnRunner{
		Model: model,
		Policy: cognition.Policy(cognitionPolicyFunc(func(context.Context, cognition.ToolCall) cognition.Decision {
			return cognition.Ask
		})),
		Tools: cognition.EchoTools{},
	}
	out, err := CollectTokens(context.Background(), r, TurnRequest{Prompt: "pay"})
	if err != nil {
		t.Fatal(err)
	}
	if !strings.Contains(out, "@@tool_call:") || !strings.Contains(out, "payment.submit") {
		t.Fatalf("pending tool not emitted: %q", out)
	}
	if !strings.Contains(out, "@@status:Waiting for your approval") {
		t.Fatalf("missing approval status: %q", out)
	}
}

type cognitionModelFunc func(context.Context, cognition.Turn) (<-chan cognition.ModelEvent, error)

func (f cognitionModelFunc) Stream(ctx context.Context, turn cognition.Turn) (<-chan cognition.ModelEvent, error) {
	return f(ctx, turn)
}

type cognitionPolicyFunc func(context.Context, cognition.ToolCall) cognition.Decision

func (f cognitionPolicyFunc) Decide(ctx context.Context, call cognition.ToolCall) cognition.Decision {
	return f(ctx, call)
}

func TestFormatToolCallTokenFamily(t *testing.T) {
	tok := formatToolCallToken(cognition.ToolCall{Name: "x", Input: []byte(`not-json`)})
	if !strings.HasPrefix(tok, "@@tool_call:") || !strings.Contains(tok, `"_raw"`) {
		t.Fatalf("raw wrap: %q", tok)
	}
	tok2 := formatToolCallToken(cognition.ToolCall{Name: "y", Input: []byte(`{"a":1}`)})
	if !strings.Contains(tok2, `"a":1`) {
		t.Fatalf("json args: %q", tok2)
	}
}
