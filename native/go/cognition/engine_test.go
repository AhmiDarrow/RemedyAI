package cognition

import (
	"bytes"
	"context"
	"errors"
	"fmt"
	"strings"
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
	// absolute ceiling must keep going well past that — but only on productive
	// mutate/verify tools (explore thrash must not extend forever).
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n > 140 {
				return events(ModelEvent{Text: "finished", Done: true}), nil
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
			MaxIterations:     10_000,
			MaxToolCalls:      100_000,
			SoftEpochSteps:    50,
			MaxRepeatedBatch:  100,
			RepeatNudgeBudget: -1,
			MaxExploreStreak:  100, // not under test here
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
	if !strings.Contains(out.Text, "finished") {
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
	if cfg.SoftEpochSteps < 16 || cfg.SoftEpochSteps > 128 {
		t.Fatalf("SoftEpochSteps=%d want lean default around 64", cfg.SoftEpochSteps)
	}
	if cfg.MaxParallelTools < 16 {
		t.Fatalf("MaxParallelTools=%d", cfg.MaxParallelTools)
	}
	if cfg.KeepLastResults < 8 {
		t.Fatalf("KeepLastResults=%d", cfg.KeepLastResults)
	}
	if cfg.MaxResultChars < 8_000 {
		t.Fatalf("MaxResultChars=%d", cfg.MaxResultChars)
	}
	if cfg.MaxAssistantChars < 512 {
		t.Fatalf("MaxAssistantChars=%d", cfg.MaxAssistantChars)
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
			MaxExploreStreak:  100,
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

func TestEngineExploreThrashNudgesThenStops(t *testing.T) {
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			return events(ModelEvent{ToolCall: &ToolCall{
				ID: fmt.Sprintf("%d", n), Name: "workspace.read",
				Input: []byte(fmt.Sprintf(`{"path":"f%d"}`, n)),
			}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("empty")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{
			MaxIterations:     50,
			MaxToolCalls:      50,
			SoftEpochSteps:    -1,
			MaxRepeatedBatch:  100,
			RepeatNudgeBudget: -1,
			MaxExploreStreak:  3,
		},
	}
	out := engine.Run(context.Background(), "review the Tailscale connect stack")
	if !errors.Is(out.Err, ErrNoProgress) {
		t.Fatalf("explore thrash want ErrNoProgress, got %v after %d rounds text=%q", out.Err, rounds.Load(), out.Text)
	}
	if !strings.Contains(out.Text, "[DELIVER]") {
		t.Fatalf("expected DELIVER nudge for review goal, text=%q", out.Text)
	}
}

func TestEngineExploreDoesNotExtendToolCeiling(t *testing.T) {
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			return events(ModelEvent{ToolCall: &ToolCall{
				ID: fmt.Sprintf("%d", n), Name: "workspace.search",
				Input: []byte(fmt.Sprintf(`{"q":"tailscale%d"}`, n)),
			}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("[]")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{
			MaxIterations:     20,
			MaxToolCalls:      5,
			SoftEpochSteps:    4,
			MaxRepeatedBatch:  100,
			RepeatNudgeBudget: -1,
			MaxExploreStreak:  100, // disable thrash stop; test ceiling only
		},
	}
	out := engine.Run(context.Background(), "build a feature")
	if !errors.Is(out.Err, ErrToolCallLimit) {
		t.Fatalf("explore-only must hit tool ceiling, got %v after %d", out.Err, rounds.Load())
	}
}

func TestClassifyToolKinds(t *testing.T) {
	if ClassifyTool("workspace.read") != ToolExplore {
		t.Fatal("read")
	}
	if ClassifyTool("workspace.edit") != ToolMutate {
		t.Fatal("edit")
	}
	if ClassifyTool("mission_verify") != ToolVerify {
		t.Fatal("verify")
	}
	if !goalLooksLikeBuild("fix the connect crash") {
		t.Fatal("build goal")
	}
	if goalLooksLikeBuild("what is Tailscale?") {
		t.Fatal("question should not look like build")
	}
}

func TestEngineLengthContinueDoesNotStop(t *testing.T) {
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n == 1 {
				return events(ModelEvent{Text: "partial…", Done: true, Truncated: true}), nil
			}
			return events(ModelEvent{Text: "finished", Done: true}), nil
		}),
		Tools:  toolFunc(func(context.Context, ToolCall) ToolResult { return ToolResult{} }),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 10, SoftEpochSteps: -1, MaxLengthContinues: 4},
	}
	out := engine.Run(context.Background(), "goal")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	if rounds.Load() < 2 {
		t.Fatalf("expected length continue, rounds=%d", rounds.Load())
	}
	if !strings.Contains(out.Text, "finished") {
		t.Fatalf("text=%q", out.Text)
	}
}

func TestEngineSoftEpochLeavesNoOrphanToolResults(t *testing.T) {
	var rounds atomic.Int32
	var afterEpochResults int
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n > 4 {
				return events(ModelEvent{Text: "done", Done: true}), nil
			}
			return events(ModelEvent{ToolCall: &ToolCall{
				ID: fmt.Sprintf("%d", n), Name: "workspace.read", Input: []byte(`{"path":"a"}`),
			}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("body")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{
			MaxIterations: 20, SoftEpochSteps: 2, MaxRepeatedBatch: 50, RepeatNudgeBudget: -1,
		},
	}
	engine.EpochHook = func(epoch, total, tools int, turn *Turn) {
		afterEpochResults = len(turn.Results)
		if len(turn.Calls) != 0 {
			t.Fatalf("epoch must clear Calls, got %d", len(turn.Calls))
		}
	}
	out := engine.Run(context.Background(), "build")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	if afterEpochResults != 0 {
		t.Fatalf("epoch must clear Results (no orphan tools), got %d", afterEpochResults)
	}
	if !strings.Contains(out.Text, "Epoch") && !strings.Contains(out.Text, "done") {
		t.Fatalf("expected checkpoint or done in text=%q", out.Text)
	}
}

func TestCapResultsHeadTail(t *testing.T) {
	big := bytes.Repeat([]byte("x"), 10_000)
	out := capResults([]ToolResult{{Name: "r", Output: big}}, 1_000)
	if len(out[0].Output) > 1_200 {
		t.Fatalf("len=%d", len(out[0].Output))
	}
	if !bytes.Contains(out[0].Output, []byte("truncated")) {
		t.Fatalf("missing truncate marker: %s", out[0].Output[:80])
	}
}

func TestEngineContinueGateRearmsThenStops(t *testing.T) {
	var rounds atomic.Int32
	var rearmAsks atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n == 1 {
				return events(ModelEvent{ToolCall: &ToolCall{ID: "1", Name: "workspace.read", Input: []byte(`{}`)}}), nil
			}
			return events(ModelEvent{Text: "summary", Done: true}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("ok")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 20, SoftEpochSteps: -1, MaxRearms: 2},
		ContinueGate: func(_ context.Context, _ Turn, toolCount int) (bool, string) {
			rearmAsks.Add(1)
			if rearmAsks.Load() <= 2 {
				return true, "keep going"
			}
			return false, ""
		},
	}
	out := engine.Run(context.Background(), "build it")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	if rearmAsks.Load() < 2 {
		t.Fatalf("expected re-arm asks, got %d", rearmAsks.Load())
	}
	if rounds.Load() < 3 {
		t.Fatalf("rounds=%d", rounds.Load())
	}
	if !strings.Contains(out.Text, "keep going") {
		t.Fatalf("text=%q", out.Text)
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
