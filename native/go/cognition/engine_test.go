package cognition

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync/atomic"
	"testing"
	"time"
	"unicode/utf8"
)

type modelFunc func(context.Context, Turn) (<-chan ModelEvent, error)

func (f modelFunc) Stream(ctx context.Context, turn Turn) (<-chan ModelEvent, error) {
	return f(ctx, turn)
}

// ContextWindow reports "unknown" so the engine assumes DefaultContextWindow.
func (modelFunc) ContextWindow() int { return 0 }

// windowedModel is a modelFunc that reports a small context window, so budget
// and compaction behaviour can be driven in a test.
type windowedModel struct {
	stream func(context.Context, Turn) (<-chan ModelEvent, error)
	window int
}

func (m windowedModel) Stream(ctx context.Context, turn Turn) (<-chan ModelEvent, error) {
	return m.stream(ctx, turn)
}

func (m windowedModel) ContextWindow() int { return m.window }

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

// transcriptText flattens a transcript for substring assertions.
func transcriptText(msgs []Message) string {
	var b strings.Builder
	for _, m := range msgs {
		b.WriteString(string(m.Role))
		b.WriteString(": ")
		for _, block := range m.Blocks {
			switch block.Type {
			case BlockText, BlockThinking:
				b.WriteString(block.Text)
			case BlockToolUse:
				b.WriteString(block.Name)
				b.WriteString(string(block.Input))
			case BlockToolResult:
				b.WriteString(blockText(block.Content))
			}
			b.WriteByte('\n')
		}
	}
	return b.String()
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
	// user goal, assistant(text+2 tool_use), user(2 tool_result), assistant(done)
	if len(out.Messages) != 4 {
		t.Fatalf("transcript = %s", transcriptText(out.Messages))
	}
	if len(out.Messages[1].ToolUses()) != 2 || !out.Messages[2].HasToolResults() {
		t.Fatalf("round shape wrong: %s", transcriptText(out.Messages))
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

func TestEngineDeniedToolStillGetsAResultBlock(t *testing.T) {
	// Every tool_use must be answered: a denied call is an is_error result, not
	// a missing one (an unanswered tool call is a provider 400).
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			if rounds.Add(1) == 1 {
				return events(
					ModelEvent{ToolCall: &ToolCall{ID: "1", Name: "workspace.read", Input: []byte(`{"path":"a"}`)}},
					ModelEvent{ToolCall: &ToolCall{ID: "2", Name: "payment.submit", Input: []byte(`{}`)}},
				), nil
			}
			return events(ModelEvent{Text: "ok", Done: true}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("body")}
		}),
		Policy: policyFunc(func(_ context.Context, c ToolCall) Decision {
			if c.Name == "payment.submit" {
				return Deny
			}
			return Allow
		}),
	}
	out := engine.Run(context.Background(), "go")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	results := out.Messages[2].Blocks
	if len(results) != 2 || results[0].ToolUseID != "1" || results[1].ToolUseID != "2" {
		t.Fatalf("results not paired in call order: %#v", results)
	}
	if results[0].IsError || !results[1].IsError {
		t.Fatalf("deny must be an is_error result: %#v", results)
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

func TestEngineRepeatGateNudgesOnceThenStops(t *testing.T) {
	// Design 2.6: an identical batch three times gets one nudge; identical
	// again ends the turn. The refused batch never runs.
	var rounds atomic.Int32
	var executed atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			rounds.Add(1)
			return events(ModelEvent{ToolCall: &ToolCall{
				ID: "c", Name: "workspace.read", Input: []byte(`{"path":"a","offset":1}`),
			}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			executed.Add(1)
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("body")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 20, SoftEpochSteps: -1},
	}
	out := engine.Run(context.Background(), "read it")
	if !errors.Is(out.Err, ErrNoProgress) {
		t.Fatalf("want ErrNoProgress, got %v after %d rounds", out.Err, rounds.Load())
	}
	if rounds.Load() != 4 {
		t.Fatalf("want three identical batches, one nudge, then a stop: rounds=%d", rounds.Load())
	}
	if executed.Load() != 2 {
		t.Fatalf("the nudged batch must not run: executed=%d", executed.Load())
	}
	if !strings.Contains(transcriptText(out.Messages), "[Nudge]") {
		t.Fatalf("nudge missing from the transcript: %s", transcriptText(out.Messages))
	}
}

func TestNoveltyKeyIsOrderAndKeyStable(t *testing.T) {
	a := []ToolCall{
		{Name: "workspace.read", Input: []byte(`{"path":"a","offset":1}`)},
		{Name: "workspace.grep", Input: []byte(`{"q":"x"}`)},
	}
	b := []ToolCall{
		{Name: "workspace.grep", Input: []byte(`{"q":"x"}`)},
		{Name: "workspace.read", Input: []byte(`{"offset":1,"path":"a"}`)},
	}
	if noveltyKey(a) != noveltyKey(b) {
		t.Fatalf("novelty key must ignore call order and JSON key order:\n%q\n%q", noveltyKey(a), noveltyKey(b))
	}
	c := []ToolCall{{Name: "workspace.read", Input: []byte(`{"path":"b"}`)}}
	if noveltyKey(a) == noveltyKey(c) {
		t.Fatal("different arguments must be novel")
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
		},
	}
	var epochs atomic.Int32
	engine.EpochHook = func(epoch, total, tools int, _ []string, _ *Turn) {
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
	if cfg.MaxRepeatedBatch != 2 || cfg.RepeatNudgeBudget != 1 {
		t.Fatalf("repeat gate = %d batches / %d nudges", cfg.MaxRepeatedBatch, cfg.RepeatNudgeBudget)
	}
}

func TestTranscriptBudgetFromContextWindow(t *testing.T) {
	if got := transcriptBudget(0); got != int(float64(DefaultContextWindow)*0.75)-16_000 {
		t.Fatalf("cloud budget=%d", got)
	}
	if got := transcriptBudget(8192); got != 2048 {
		t.Fatalf("small local window budget=%d want the window/4 floor", got)
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

func TestEngineExploreOnlyStreakNeverEndsTurn(t *testing.T) {
	// Rewritten from TestEngineExploreThrashNudgesThenStops: reading many
	// different files is how a review is done, not thrash. Only a repeated
	// identical batch is a loop (design 2.6).
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n > 30 {
				return events(ModelEvent{Text: "here is the review", Done: true}), nil
			}
			return events(ModelEvent{ToolCall: &ToolCall{
				ID: fmt.Sprintf("%d", n), Name: "workspace.read",
				Input: []byte(fmt.Sprintf(`{"path":"f%d"}`, n)),
			}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("empty")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 60, MaxToolCalls: 60, SoftEpochSteps: -1},
	}
	out := engine.Run(context.Background(), "review the Tailscale connect stack")
	if out.Err != nil {
		t.Fatalf("explore-only must not end the turn: %v after %d rounds", out.Err, rounds.Load())
	}
	if !strings.Contains(out.Text, "here is the review") {
		t.Fatalf("text=%q", out.Text)
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
	if ClassifyTool("read") != ToolExplore {
		t.Fatal("advertised read")
	}
	if ClassifyTool("workspace.edit") != ToolMutate {
		t.Fatal("edit")
	}
	if ClassifyTool("edit") != ToolMutate {
		t.Fatal("advertised edit")
	}
	if ClassifyTool("write") != ToolMutate {
		t.Fatal("advertised write")
	}
	if ClassifyTool("mission_verify") != ToolVerify {
		t.Fatal("verify")
	}
	if ClassifyTool("bash") != ToolVerify {
		t.Fatal("bash")
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

func TestEngineSoftEpochKeepsTheTranscript(t *testing.T) {
	// Rewritten from TestEngineSoftEpochLeavesNoOrphanToolResults: a soft epoch
	// no longer clears observations — it only refreshes the brief. Round 1's
	// tool result is still there after the epoch.
	var rounds atomic.Int32
	var epochTranscript []Message
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n > 4 {
				return events(ModelEvent{Text: "done", Done: true}), nil
			}
			return events(ModelEvent{ToolCall: &ToolCall{
				ID: fmt.Sprintf("%d", n), Name: "workspace.read",
				Input: []byte(fmt.Sprintf(`{"path":"a%d"}`, n)),
			}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("body-" + c.ID)}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{
			MaxIterations: 20, SoftEpochSteps: 2, MaxRepeatedBatch: 50, RepeatNudgeBudget: -1,
		},
	}
	engine.EpochHook = func(epoch, total, tools int, ledger []string, turn *Turn) {
		if epoch == 1 {
			epochTranscript = turn.Messages
		}
		if len(ledger) == 0 {
			t.Errorf("epoch %d ledger empty", epoch)
		}
		turn.System = fmt.Sprintf("brief-%d", epoch)
	}
	out := engine.Run(context.Background(), "build")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	if !strings.Contains(transcriptText(epochTranscript), "body-1") {
		t.Fatalf("epoch trimmed the transcript: %s", transcriptText(epochTranscript))
	}
	if !strings.Contains(transcriptText(out.Messages), "body-1") {
		t.Fatalf("round 1 result lost: %s", transcriptText(out.Messages))
	}
}

func TestEngineEpochHookSystemIsAppliedToLaterRounds(t *testing.T) {
	var systems []string
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(_ context.Context, turn Turn) (<-chan ModelEvent, error) {
			systems = append(systems, turn.System)
			n := rounds.Add(1)
			if n > 3 {
				return events(ModelEvent{Text: "done", Done: true}), nil
			}
			return events(ModelEvent{ToolCall: &ToolCall{ID: fmt.Sprintf("%d", n), Name: "workspace.edit", Input: []byte(fmt.Sprintf(`{"path":"f%d"}`, n))}}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("ok")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 10, SoftEpochSteps: 2, MaxRepeatedBatch: 50, RepeatNudgeBudget: -1},
		EpochHook: func(epoch, _, _ int, _ []string, turn *Turn) {
			turn.System = "brief"
		},
	}
	out := engine.RunTurn(context.Background(), Turn{System: "base", Messages: []Message{UserText("build")}})
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	if systems[0] != "base" || systems[len(systems)-1] != "brief" {
		t.Fatalf("systems=%q", systems)
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
		ContinueGate: func(_ context.Context, _ Turn, toolCount int, _ []ToolResult, _ bool) (bool, string) {
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
	if !strings.Contains(transcriptText(out.Messages), "[Re-arm] keep going") {
		t.Fatalf("re-arm missing from the transcript: %s", transcriptText(out.Messages))
	}
}

func TestEngineContinueGateSeesEvidence(t *testing.T) {
	// A mutation with no green verify after it must reach the gate as
	// verifySeen=false; a passing verify flips it back.
	var rounds atomic.Int32
	var seen []bool
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			switch rounds.Add(1) {
			case 1:
				return events(ModelEvent{ToolCall: &ToolCall{ID: "1", Name: "workspace.edit", Input: []byte(`{"path":"a"}`)}}), nil
			case 2:
				return events(ModelEvent{Text: "wrote it", Done: true}), nil
			case 3:
				return events(ModelEvent{ToolCall: &ToolCall{ID: "2", Name: "shell.exec", Input: []byte(`{"argv":["go","test"]}`)}}), nil
			default:
				return events(ModelEvent{Text: "green", Done: true}), nil
			}
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte(`{"exit_code":0}`)}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 10, SoftEpochSteps: -1, MaxRearms: 4},
		ContinueGate: func(_ context.Context, _ Turn, _ int, last []ToolResult, verifySeen bool) (bool, string) {
			seen = append(seen, verifySeen)
			return len(seen) == 1, "run the tests"
		},
	}
	out := engine.Run(context.Background(), "fix it")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	if len(seen) != 2 || seen[0] || !seen[1] {
		t.Fatalf("verifySeen sequence = %v (want [false true])", seen)
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

func TestEngineFillsMissingToolCallIDs(t *testing.T) {
	var seen []Turn
	engine := Engine{
		Model: modelFunc(func(_ context.Context, turn Turn) (<-chan ModelEvent, error) {
			seen = append(seen, turn)
			if len(seen) == 1 {
				return events(
					ModelEvent{ToolCall: &ToolCall{Name: "workspace.read", Input: []byte(`{"path":"a"}`)}},
					ModelEvent{ToolCall: &ToolCall{Name: "workspace.read", Input: []byte(`{"path":"b"}`)}},
				), nil
			}
			return events(ModelEvent{Text: "ok", Done: true}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("x")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
	}
	out := engine.Run(context.Background(), "read both")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	uses := out.Messages[1].ToolUses()
	results := out.Messages[2].Blocks
	if len(uses) != 2 || len(results) != 2 {
		t.Fatalf("shape: %s", transcriptText(out.Messages))
	}
	if uses[0].ID == "" || uses[0].ID == uses[1].ID {
		t.Fatalf("ids must be unique and non-empty: %q %q", uses[0].ID, uses[1].ID)
	}
	if results[0].ToolUseID != uses[0].ID || results[1].ToolUseID != uses[1].ID {
		t.Fatalf("results not paired to their calls: %#v", results)
	}
}

func TestTranscriptStillHoldsRoundOneAtRoundForty(t *testing.T) {
	// The defect this rebuild fixes: at round 40 the model used to see only the
	// previous tool batch. It must now see round 1's tool result.
	var rounds atomic.Int32
	var lastTurn Turn
	engine := Engine{
		Model: modelFunc(func(_ context.Context, turn Turn) (<-chan ModelEvent, error) {
			lastTurn = turn
			n := rounds.Add(1)
			if n > 40 {
				return events(ModelEvent{Text: "done", Done: true}), nil
			}
			return events(ModelEvent{
				Text: fmt.Sprintf("step %d ", n),
				ToolCall: &ToolCall{
					ID: fmt.Sprintf("c%d", n), Name: "workspace.read",
					Input: []byte(fmt.Sprintf(`{"path":"f%d"}`, n)),
				},
			}), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("contents-of-" + c.ID)}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 100, MaxToolCalls: 100, SoftEpochSteps: -1, MaxRepeatedBatch: 100, RepeatNudgeBudget: -1},
	}
	out := engine.Run(context.Background(), "read the whole tree")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	round40 := transcriptText(lastTurn.Messages)
	for _, want := range []string{"read the whole tree", "contents-of-c1", "step 1 ", "contents-of-c39"} {
		if !strings.Contains(round40, want) {
			t.Fatalf("round 40 lost %q from the transcript", want)
		}
	}
	// 1 goal + 40 rounds × (assistant + tool results) + the final assistant.
	if len(lastTurn.Messages) != 1+2*40 {
		t.Fatalf("transcript length=%d", len(lastTurn.Messages))
	}
}

func TestEngineNudgeRoundKeepsObservations(t *testing.T) {
	// Rewritten from TestEngineNudgesKeepObservations: nudges are appended
	// messages now, and the tool results of earlier rounds stay in place.
	var rounds atomic.Int32
	var seen []Turn
	engine := Engine{
		Model: modelFunc(func(_ context.Context, turn Turn) (<-chan ModelEvent, error) {
			seen = append(seen, turn)
			switch rounds.Add(1) {
			case 1, 2, 3:
				return events(ModelEvent{ToolCall: &ToolCall{ID: "1", Name: "workspace.read", Input: []byte(`{"path":"a"}`)}}), nil
			case 4:
				return events(ModelEvent{Text: "cut", Done: true, Truncated: true}), nil
			case 5:
				return events(ModelEvent{Text: "summary", Done: true}), nil
			default:
				return events(ModelEvent{Text: "final", Done: true}), nil
			}
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("body")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 20, SoftEpochSteps: -1, MaxRepeatedBatch: 2, RepeatNudgeBudget: 1, MaxRearms: 1},
		ContinueGate: func(_ context.Context, _ Turn, _ int, last []ToolResult, _ bool) (bool, string) {
			if len(last) != 1 || last[0].Name != "workspace.read" {
				t.Fatalf("gate must see the last results, got %#v", last)
			}
			return true, "one more"
		},
	}
	out := engine.Run(context.Background(), "goal")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	// Round 4 follows the repeat nudge, 5 the length-continue, 6 the re-arm.
	for _, round := range []int{3, 4, 5} {
		if round >= len(seen) {
			t.Fatalf("only %d rounds seen", len(seen))
		}
		text := transcriptText(seen[round].Messages)
		if !strings.Contains(text, "body") {
			t.Fatalf("round %d lost its observations: %s", round+1, text)
		}
	}
	if !strings.Contains(transcriptText(seen[3].Messages), "[Nudge]") ||
		!strings.Contains(transcriptText(seen[4].Messages), "[Continue]") ||
		!strings.Contains(transcriptText(seen[5].Messages), "[Re-arm] one more") {
		t.Fatalf("nudges missing: %s", transcriptText(seen[5].Messages))
	}
}

func TestClassifyComputerUseIsMutate(t *testing.T) {
	for _, name := range []string{
		"computer.click", "computer.type", "computer.key", "computer.key_hold", "computer.scroll",
		"computer.drag", "computer.focus", "computer.window", "computer.navigate", "computer.uia.action",
		"clipboard.write",
	} {
		if ClassifyTool(name) != ToolMutate {
			t.Fatalf("%s must be ToolMutate", name)
		}
	}
	if ClassifyTool("computer.screenshot") != ToolExplore {
		t.Fatal("screenshot stays explore")
	}
}

func TestEngineComputerUseRunsAsLongAsItProgresses(t *testing.T) {
	var rounds atomic.Int32
	engine := Engine{
		Model: modelFunc(func(context.Context, Turn) (<-chan ModelEvent, error) {
			n := rounds.Add(1)
			if n > 12 {
				return events(ModelEvent{Text: "done", Done: true}), nil
			}
			return events(
				ModelEvent{ToolCall: &ToolCall{ID: fmt.Sprintf("s%d", n), Name: "computer.screenshot", Input: []byte(`{}`)}},
				ModelEvent{ToolCall: &ToolCall{ID: fmt.Sprintf("c%d", n), Name: "computer.click", Input: []byte(fmt.Sprintf(`{"x":%d}`, n))}},
			), nil
		}),
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte("ok")}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 40, SoftEpochSteps: -1},
	}
	out := engine.Run(context.Background(), "open the settings window")
	if out.Err != nil {
		t.Fatalf("computer-use turn killed: %v", out.Err)
	}
}

func TestEngineCompactsOverBudgetAndKeepsTheGoal(t *testing.T) {
	var rounds atomic.Int32
	var turns []Turn
	body := strings.Repeat("failing test output. ", 400) // ~8k chars per result
	model := windowedModel{window: 8192, stream: func(_ context.Context, turn Turn) (<-chan ModelEvent, error) {
		turns = append(turns, turn)
		n := rounds.Add(1)
		if n > 12 {
			return events(ModelEvent{Text: "done", Done: true}), nil
		}
		return events(
			ModelEvent{Text: fmt.Sprintf("Running the suite for step %d. More prose here.", n)},
			ModelEvent{ToolCall: &ToolCall{
				ID: fmt.Sprintf("c%d", n), Name: "shell.exec",
				Input: []byte(fmt.Sprintf(`{"argv":["go","test","./pkg%d"]}`, n)),
			}},
		), nil
	}}
	engine := Engine{
		Model: model,
		Tools: toolFunc(func(_ context.Context, c ToolCall) ToolResult {
			return ToolResult{ID: c.ID, Name: c.Name, Output: []byte(`{"exit_code":1,"stdout":"` + body + `"}`)}
		}),
		Policy: policyFunc(func(context.Context, ToolCall) Decision { return Allow }),
		Config: Config{MaxIterations: 40, MaxToolCalls: 100, SoftEpochSteps: -1, MaxRepeatedBatch: 100, RepeatNudgeBudget: -1},
	}
	out := engine.Run(context.Background(), "make the suite green")
	if out.Err != nil {
		t.Fatalf("err=%v", out.Err)
	}
	last := turns[len(turns)-1]
	// Twelve uncompacted rounds would be 25 messages and ~24k tokens; the kept
	// tail is deliberately verbatim, so the bound is "stops growing", not "fits".
	if len(last.Messages) > compactKeepTail+3 {
		t.Fatalf("compaction did not bound the transcript: %d messages, ~%d tokens",
			len(last.Messages), estimateTokens(last.Messages))
	}
	if got := estimateTokens(last.Messages); got > 12_000 {
		t.Fatalf("transcript still ~%d tokens after compaction", got)
	}
	text := transcriptText(last.Messages)
	if !strings.Contains(text, "make the suite green") {
		t.Fatalf("compaction dropped the goal: %s", text[:400])
	}
	if !strings.Contains(text, compactedHeader) {
		t.Fatalf("no working-set message in the transcript: %s", text[:400])
	}
	if !strings.Contains(text, "go test ./pkg1") {
		t.Fatalf("working set lost the commands that ran: %s", text)
	}
	if last.Messages[0].Role != RoleUser || !strings.Contains(last.Messages[0].Text(), "make the suite green") {
		t.Fatalf("first message must stay the goal: %#v", last.Messages[0])
	}
}

func TestCompactTranscriptKeepsGoalAndLastEight(t *testing.T) {
	msgs := []Message{UserText("the goal")}
	for i := 1; i <= 12; i++ {
		msgs = append(msgs, Message{Role: RoleAssistant, Blocks: []Block{
			TextBlock(fmt.Sprintf("Round %d decision. Details follow.", i)),
			{Type: BlockToolUse, ID: fmt.Sprintf("c%d", i), Name: "workspace.edit", Input: json.RawMessage(fmt.Sprintf(`{"path":"f%d.go"}`, i))},
		}})
		msgs = append(msgs, Message{Role: RoleUser, Blocks: []Block{
			{Type: BlockToolResult, ToolUseID: fmt.Sprintf("c%d", i), Content: []Block{TextBlock(fmt.Sprintf("wrote f%d.go", i))}},
		}})
	}
	out := compactTranscript(msgs)
	if len(out) >= len(msgs) {
		t.Fatalf("nothing compacted: %d → %d", len(msgs), len(out))
	}
	if out[0].Text() != "the goal" {
		t.Fatalf("goal dropped: %#v", out[0])
	}
	if !strings.Contains(out[1].Text(), compactedHeader) {
		t.Fatalf("second message must be the working set: %q", out[1].Text())
	}
	if !strings.Contains(out[1].Text(), "f1.go") || !strings.Contains(out[1].Text(), "Round 1 decision.") {
		t.Fatalf("working set missing files/decisions: %q", out[1].Text())
	}
	tail := out[len(out)-8:]
	want := msgs[len(msgs)-8:]
	for i := range tail {
		if transcriptText(tail[i:i+1]) != transcriptText(want[i:i+1]) {
			t.Fatalf("tail message %d was rewritten", i)
		}
	}
	// The tail never begins with tool results whose call was dropped.
	first := out[2]
	if first.Role == RoleUser && first.HasToolResults() {
		t.Fatal("compaction orphaned a tool result")
	}
}

func TestCompactTranscriptKeepsCurrentGoalAfterHistory(t *testing.T) {
	msgs := []Message{UserText("remember the milk")}
	for i := 0; i < 10; i++ {
		msgs = append(msgs, Message{Role: RoleAssistant, Blocks: []Block{TextBlock(fmt.Sprintf("old %d", i))}})
		msgs = append(msgs, UserText(fmt.Sprintf("history %d", i)))
	}
	msgs = append(msgs, UserText("do X now"))
	for i := 1; i <= 10; i++ {
		msgs = append(msgs, Message{Role: RoleAssistant, Blocks: []Block{
			TextBlock(fmt.Sprintf("Round %d.", i)),
			{Type: BlockToolUse, ID: fmt.Sprintf("n%d", i), Name: "edit", Input: json.RawMessage(`{"path":"a.go"}`)},
		}})
		msgs = append(msgs, Message{Role: RoleUser, Blocks: []Block{
			{Type: BlockToolResult, ToolUseID: fmt.Sprintf("n%d", i), Content: []Block{TextBlock("ok")}},
		}})
	}
	out := compactTranscript(msgs)
	joined := transcriptText(out)
	if !strings.Contains(joined, "remember the milk") {
		t.Fatalf("session opener dropped: %s", joined)
	}
	if !strings.Contains(joined, "do X now") {
		t.Fatalf("current goal dropped: %s", joined)
	}
}

func TestWorkingSetRecordsCommandsFailuresAndTodo(t *testing.T) {
	msgs := []Message{
		{Role: RoleAssistant, Blocks: []Block{
			TextBlock("I will run the tests. Then fix them."),
			{Type: BlockToolUse, ID: "c1", Name: "shell.exec", Input: json.RawMessage(`{"argv":["go","test","./..."]}`)},
			{Type: BlockToolUse, ID: "c2", Name: "todo", Input: json.RawMessage(`{"items":[{"id":"1","text":"fix engine","status":"in_progress"}]}`)},
			{Type: BlockToolUse, ID: "c3", Name: "workspace.read", Input: json.RawMessage(`{"path":"engine.go"}`)},
		}},
		{Role: RoleUser, Blocks: []Block{
			{Type: BlockToolResult, ToolUseID: "c1", IsError: true, Content: []Block{TextBlock(`{"exit_code":1,"stdout":"FAIL engine_test.go:42"}`)}},
			{Type: BlockToolResult, ToolUseID: "c2", Content: []Block{TextBlock("ok")}},
			{Type: BlockToolResult, ToolUseID: "c3", Content: []Block{TextBlock("package cognition")}},
		}},
	}
	ws := buildWorkingSet(msgs)
	rendered := ws.render(len(msgs))
	for _, want := range []string{
		"go test ./...", "exit 1", "FAIL engine_test.go:42",
		"fix engine", "engine.go", "I will run the tests.",
	} {
		if !strings.Contains(rendered, want) {
			t.Fatalf("working set missing %q:\n%s", want, rendered)
		}
	}
	if len(ws.read) != 1 || ws.read[0].hash == "" {
		t.Fatalf("read files need a content hash: %#v", ws.read)
	}
	// Deterministic: same input, same bytes.
	if buildWorkingSet(msgs).render(len(msgs)) != rendered {
		t.Fatal("working set is not deterministic")
	}
}

func TestEstimateTokensChargesForImages(t *testing.T) {
	text := []Message{UserText(strings.Repeat("a", 400))}
	if got := estimateTokens(text); got < 100 || got > 120 {
		t.Fatalf("text tokens=%d", got)
	}
	withImage := []Message{{Role: RoleUser, Blocks: []Block{ImageBlock("image/png", bytes.Repeat([]byte{0}, 64))}}}
	if got := estimateTokens(withImage); got < imageTokenCost {
		t.Fatalf("image tokens=%d", got)
	}
}

func TestByteSafeSlicing(t *testing.T) {
	b := []byte(strings.Repeat("日本語", 400)) // 3 bytes per rune
	clipped := clipBytes(b, 301)
	if !utf8.Valid(clipped) {
		t.Fatalf("clipBytes split a rune: %q", clipped)
	}
	line := outcomeLine(ToolResult{Name: "t", Output: []byte(strings.Repeat("ü", 200))})
	if !utf8.ValidString(line) {
		t.Fatalf("outcomeLine split a rune: %q", line)
	}
	if got := clipRunes(strings.Repeat("é", 50), 7); !utf8.ValidString(got) || len([]rune(got)) != 8 {
		t.Fatalf("clipRunes=%q", got)
	}
}
