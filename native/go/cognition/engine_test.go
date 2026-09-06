package cognition

import (
	"context"
	"errors"
	"fmt"
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

func TestEngineApprovalGateResumesAndExecutes(t *testing.T) {
	var decides atomic.Int32
	var executed atomic.Int32
	var modelCalls atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			if modelCalls.Add(1) == 1 {
				return events(ModelEvent{ToolCall: &ToolCall{ID: "1", Name: "shell.exec", Input: []byte(`{"argv":["echo"]}`)}}), nil
			}
			return events(ModelEvent{Text: "done", Done: true}), nil
		}),
		Tools: toolFunc(func(_ context.Context, call ToolCall) ToolResult {
			executed.Add(1)
			return ToolResult{ID: call.ID, Name: call.Name, Output: []byte("ok")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision {
			if decides.Add(1) == 1 {
				return Ask
			}
			return Allow
		}),
		ApprovalGate: func(context.Context, []ToolCall) error { return nil },
	}
	out := engine.Run(context.Background(), "run")
	if out.Err != nil || executed.Load() != 1 || out.Text != "done" {
		t.Fatalf("outcome=%#v executed=%d", out, executed.Load())
	}
}

func TestEngineApprovalGateDenyDoesNotExecute(t *testing.T) {
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			return events(ModelEvent{ToolCall: &ToolCall{ID: "1", Name: "shell.exec"}}), nil
		}),
		Tools: toolFunc(func(context.Context, ToolCall) ToolResult {
			t.Fatal("tool must not run after deny")
			return ToolResult{}
		}),
		Policy:       policyFunc(func(context.Context, ToolCall) Decision { return Ask }),
		ApprovalGate: func(context.Context, []ToolCall) error { return ErrOwnerDenied },
	}
	out := engine.Run(context.Background(), "run")
	if !errors.Is(out.Err, ErrOwnerDenied) || len(out.Pending) != 1 {
		t.Fatalf("outcome=%#v", out)
	}
}

func TestEngineStopsRepeatedNoProgressAndToolCeilings(t *testing.T) {
	repeating := modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
		return events(ModelEvent{ToolCall: &ToolCall{Name: "same", Input: []byte("x")}}), nil
	})
	base := Engine{Model: repeating, Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult { return ToolResult{Name: c.Name} }), Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }), Config: Config{MaxIterations: 10, MaxRepeatedBatch: 1, MaxToolCalls: 10, RepeatNudgeBudget: -1, SoftEpochSteps: -1}}
	if out := base.Run(context.Background(), "goal"); !errors.Is(out.Err, ErrNoProgress) {
		t.Fatalf("repeat = %v", out.Err)
	}
	base.Config.MaxRepeatedBatch = 10
	base.Config.MaxToolCalls = 1
	base.Config.RepeatNudgeBudget = -1
	if out := base.Run(context.Background(), "goal"); !errors.Is(out.Err, ErrToolCallLimit) {
		t.Fatalf("ceiling = %v", out.Err)
	}
}

func TestEngineSoftEpochContinuesPastOldToolCap(t *testing.T) {
	// Old default MaxToolCalls=128 would stop mid-build. Soft epochs + high
	// absolute ceiling must keep going well past that.
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n > 140 {
				return events(ModelEvent{Text: "finished", Done: true}), nil
			}
			return events(ModelEvent{ToolCall: &ToolCall{
				ID: fmt.Sprintf("%d", n), Name: "workspace.read",
				Input: []byte(fmt.Sprintf(`{"path":"f%d"}`, n)),
			}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("ok")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{
			MaxIterations:     10_000,
			MaxToolCalls:      100_000,
			SoftEpochSteps:    50,
			MaxRepeatedBatch:  100,
			RepeatNudgeBudget: -1,
		},
	}
	var epochs atomic.Int32
	engine.EpochHook = func(epoch, total, tools int, _ *Turn) {
		epochs.Add(1)
	}
	out := engine.Run(context.Background(), "build forever")
	if out.Err != nil {
		t.Fatalf("must not hit tool limit: %v after %d rounds", out.Err, rounds.Load())
	}
	if rounds.Load() <= 128 {
		t.Fatalf("expected >128 model rounds, got %d", rounds.Load())
	}
	if epochs.Load() < 2 {
		t.Fatalf("expected soft epochs, got %d", epochs.Load())
	}
	if out.Text != "finished" {
		t.Fatalf("text=%q", out.Text)
	}
}

func TestEngineDefaultCeilingsAreBuildScale(t *testing.T) {
	cfg := Config{}.normalized()
	if cfg.MaxIterations < 100_000 {
		t.Fatalf("MaxIterations=%d want build-scale absolute ceiling", cfg.MaxIterations)
	}
	if cfg.MaxToolCalls < 1_000_000 {
		t.Fatalf("MaxToolCalls=%d want build-scale absolute ceiling", cfg.MaxToolCalls)
	}
	if cfg.SoftEpochSteps < 16 {
		t.Fatalf("SoftEpochSteps=%d", cfg.SoftEpochSteps)
	}
	if cfg.MaxParallelTools < 16 {
		t.Fatalf("MaxParallelTools=%d", cfg.MaxParallelTools)
	}
	if cfg.KeepLastResults < 32 {
		t.Fatalf("KeepLastResults=%d", cfg.KeepLastResults)
	}
}

func TestEngineProgressExtendsStepCeiling(t *testing.T) {
	// Low absolute ceiling would stop mid-build without progress extension.
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n > 80 {
				return events(ModelEvent{Text: "done", Done: true}), nil
			}
			return events(ModelEvent{ToolCall: &ToolCall{
				ID: fmt.Sprintf("%d", n), Name: "workspace.edit",
				Input: []byte(fmt.Sprintf(`{"path":"f%d"}`, n)),
			}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("ok")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{
			MaxIterations:     40, // would die without extension
			MaxToolCalls:      40,
			SoftEpochSteps:    20,
			MaxRepeatedBatch:  200,
			RepeatNudgeBudget: -1,
		},
	}
	out := engine.Run(context.Background(), "long build")
	if out.Err != nil {
		t.Fatalf("progress must extend ceilings: %v after %d rounds", out.Err, rounds.Load())
	}
	if rounds.Load() <= 40 {
		t.Fatalf("expected >40 rounds via extension, got %d", rounds.Load())
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
