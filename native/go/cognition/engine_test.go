package cognition

import (
	"context"
	"errors"
	"sync/atomic"
	"testing"
	"time"
)

type modelFunc func(context.Context, Turn) (<-chan ModelEvent, error)

func (f modelFunc) Stream(ctx context.Context, turn Turn) (<-chan ModelEvent, error) {
	return f(ctx, turn)
}

type toolFunc func(context.Context, ToolCall) ToolResult

func (f toolFunc) Execute(ctx context.Context, call ToolCall) ToolResult { return f(ctx, call) }

type policyFunc func(context.Context, ToolCall) Decision

func (f policyFunc) Decide(ctx context.Context, call ToolCall) Decision { return f(ctx, call) }

func events(values ...ModelEvent) <-chan ModelEvent {
	ch := make(chan ModelEvent, len(values))
	for _, value := range values {
		ch <- value
	}
	close(ch)
	return ch
}

func TestEngineStreamsBatchesAndCompletes(t *testing.T) {
	var modelCalls atomic.Int32
	var active atomic.Int32
	var peak atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			if modelCalls.Add(1) == 1 {
				return events(ModelEvent{Text: "thinking ", ToolCall: &ToolCall{ID: "1", Name: "read"}}, ModelEvent{ToolCall: &ToolCall{ID: "2", Name: "search"}}), nil
			}
			return events(ModelEvent{Text: "done", Done: true}), nil
		}),
		Tools: toolFunc(func(_ context.Context, call ToolCall) ToolResult {
			current := active.Add(1)
			defer active.Add(-1)
			for {
				old := peak.Load()
				if current <= old || peak.CompareAndSwap(old, current) {
					break
				}
			}
			time.Sleep(time.Millisecond)
			return ToolResult{ID: call.ID, Name: call.Name, Output: []byte("ok")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }), Config: Config{MaxParallelTools: 2}, Now: func() time.Time { return time.Unix(1, 0) },
	}
	out := engine.Run(context.Background(), "goal")
	if out.Err != nil || out.Text != "thinking done" || len(out.Results) != 2 {
		t.Fatalf("outcome = %#v", out)
	}
	if peak.Load() != 2 {
		t.Fatalf("peak parallelism = %d", peak.Load())
	}
}

func TestEngineRetriesModelAndPausesForOwner(t *testing.T) {
	var attempts atomic.Int32
	engine := Engine{Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
		if attempts.Add(1) == 1 {
			return nil, errors.New("temporary")
		}
		return events(ModelEvent{ToolCall: &ToolCall{ID: "pay", Name: "payment.submit"}}), nil
	}), Tools: toolFunc(func(context.Context, ToolCall) ToolResult {
		t.Fatal("tool executed before approval")
		return ToolResult{}
	}), Policy: policyFunc(func(context.Context, ToolCall) Decision { return Ask }), Config: Config{ModelRetries: 1}}
	out := engine.Run(context.Background(), "buy")
	if !errors.Is(out.Err, ErrOwnerConfirmationNeeded) || len(out.Pending) != 1 || attempts.Load() != 2 {
		t.Fatalf("outcome = %#v attempts=%d", out, attempts.Load())
	}
}

func TestEngineStopsRepeatedNoProgressAndToolCeilings(t *testing.T) {
	repeating := modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
		return events(ModelEvent{ToolCall: &ToolCall{Name: "same", Input: []byte("x")}}), nil
	})
	base := Engine{Model: repeating, Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult { return ToolResult{Name: c.Name} }), Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }), Config: Config{MaxIterations: 10, MaxRepeatedBatch: 1, MaxToolCalls: 10}}
	if out := base.Run(context.Background(), "goal"); !errors.Is(out.Err, ErrNoProgress) {
		t.Fatalf("repeat = %v", out.Err)
	}
	base.Config.MaxRepeatedBatch = 10
	base.Config.MaxToolCalls = 1
	if out := base.Run(context.Background(), "goal"); !errors.Is(out.Err, ErrToolCallLimit) {
		t.Fatalf("ceiling = %v", out.Err)
	}
}

func TestEngineRejectsStreamClosedWithoutDone(t *testing.T) {
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			return events(ModelEvent{Text: "partial"}), nil
		}),
		Tools:  toolFunc(func(context.Context, ToolCall) ToolResult { return ToolResult{} }),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
	}
	out := engine.Run(context.Background(), "goal")
	if !errors.Is(out.Err, ErrIncompleteModelStream) || out.Text != "partial" {
		t.Fatalf("outcome = %#v", out)
	}
}

func TestEngineStillAcceptsToolCallStreamWithoutDone(t *testing.T) {
	var calls atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			if calls.Add(1) == 1 {
				return events(ModelEvent{ToolCall: &ToolCall{ID: "1", Name: "read"}}), nil
			}
			return events(ModelEvent{Text: "complete", Done: true}), nil
		}),
		Tools:  toolFunc(func(context.Context, ToolCall) ToolResult { return ToolResult{ID: "1"} }),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
	}
	out := engine.Run(context.Background(), "goal")
	if out.Err != nil || out.Text != "complete" || len(out.Results) != 1 {
		t.Fatalf("outcome = %#v", out)
	}
}
