// Package cognition implements Remedy's deterministic ReAct orchestration state machine.
package cognition

import (
	"context"
	"errors"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
)

var (
	ErrIterationLimit          = errors.New("ReAct safety iteration ceiling reached")
	ErrToolCallLimit           = errors.New("ReAct safety tool-call ceiling reached")
	ErrNoProgress              = errors.New("ReAct made no progress")
	ErrOwnerConfirmationNeeded = errors.New("owner confirmation required")
	ErrOwnerDenied             = errors.New("owner denied confirmation")
	ErrIncompleteModelStream   = errors.New("model stream closed before completion")
)

type State string

const (
	StateObserve  State = "observe"
	StateModel    State = "model"
	StatePolicy   State = "policy"
	StateAct      State = "act"
	StateUpdate   State = "update"
	StateEpoch    State = "epoch"
	StateComplete State = "complete"
	StatePaused   State = "paused"
	StateFailed   State = "failed"
)

type ToolCall struct {
	ID, Name string
	Input    []byte
}
type ToolResult struct {
	ID, Name string
	Output   []byte
	Err      string
}
type ModelEvent struct {
	Text     string
	ToolCall *ToolCall
	Done     bool
}
type Turn struct {
	Goal      string
	System    string // assembled system/soul/skills/memory; empty = prompt-only
	Text      string
	Results   []ToolResult
	// Calls are the previous iteration's tool calls (OpenAI assistant.tool_calls).
	// Required so follow-up messages can include tool_call_id for providers like DeepSeek.
	Calls     []ToolCall
	Iteration int
}

type Model interface {
	Stream(context.Context, Turn) (<-chan ModelEvent, error)
}
type ToolExecutor interface {
	Execute(context.Context, ToolCall) ToolResult
}
type Decision uint8

const (
	Allow Decision = iota
	Ask
	Deny
)

type Policy interface {
	Decide(context.Context, ToolCall) Decision
}

// ApprovalGate is invoked when policy returns Ask for one or more tools.
// Return nil after the owner has approved (policy must then Allow on re-decide).
// Return ErrOwnerDenied to stop cleanly; any other error fails the turn.
// When nil, the engine pauses with ErrOwnerConfirmationNeeded (legacy).
type ApprovalGate func(ctx context.Context, pending []ToolCall) error

// EpochHook runs at soft-epoch boundaries (context compact + continue).
// Soft epochs never stop the turn — they only slim context so long builds
// can run for as many tool rounds as the work needs.
type EpochHook func(epoch, totalSteps, toolCalls int, turn *Turn)

// Config controls ReAct pacing. Soft epochs compact and continue; absolute
// ceilings exist only as pathological-loop safety nets — not task budgets.
// Same operating model as a Build agent: run until the request is finished.
type Config struct {
	// MaxIterations is the absolute safety ceiling on model rounds (default 10000).
	MaxIterations int
	// MaxToolCalls is the absolute safety ceiling on tool invocations (default 100000).
	MaxToolCalls int
	// SoftEpochSteps triggers compact+continue every N model rounds (default 256).
	// Zero disables soft epochs (absolute ceiling only).
	SoftEpochSteps int
	// MaxStaleEpochs stops only after this many consecutive soft epochs with
	// zero tool activity (default 8). Failed tools still count as activity.
	MaxStaleEpochs int
	// KeepLastResults is how many tool results to retain across a soft epoch (default 32).
	KeepLastResults int
	MaxParallelTools int
	MaxRepeatedBatch int
	// RepeatNudgeBudget allows identical tool batches this many times with a
	// nudge before ErrNoProgress (default 2).
	RepeatNudgeBudget int
	ModelRetries      int
	RetryBackoff      time.Duration
}

func envInt(name string, def, lo, hi int) int {
	raw := strings.TrimSpace(os.Getenv(name))
	if raw == "" {
		return def
	}
	n, err := strconv.Atoi(raw)
	if err != nil {
		return def
	}
	if n < lo {
		return lo
	}
	if n > hi {
		return hi
	}
	return n
}

func (c Config) normalized() Config {
	// Absolute safety ceilings — pathological loops only, not a task budget.
	// Defaults are build-scale: months of real coding happen across soft epochs
	// with progress-extends-budget (see RunTurn); these numbers are a last resort.
	if c.MaxIterations <= 0 {
		c.MaxIterations = envInt("REMEDY_REACT_MAX_TOTAL_STEPS", 1_000_000, 64, 10_000_000)
	}
	if c.MaxToolCalls <= 0 {
		c.MaxToolCalls = envInt("REMEDY_REACT_MAX_TOOL_CALLS", 10_000_000, 64, 100_000_000)
	}
	if c.SoftEpochSteps < 0 {
		c.SoftEpochSteps = 0
	} else if c.SoftEpochSteps == 0 {
		// 0 in zero-value Config means "use default"; explicit disable via -1 above.
		c.SoftEpochSteps = envInt("REMEDY_REACT_EPOCH_STEPS", 256, 16, 2_000)
	}
	if c.MaxStaleEpochs <= 0 {
		c.MaxStaleEpochs = envInt("REMEDY_REACT_MAX_STALE_EPOCHS", 8, 1, 50)
	}
	if c.KeepLastResults <= 0 {
		c.KeepLastResults = 64
	}
	if c.MaxParallelTools <= 0 {
		c.MaxParallelTools = envInt("REMEDY_MAX_PARALLEL_TOOLS", 32, 4, 64)
	}
	if c.MaxRepeatedBatch <= 0 {
		c.MaxRepeatedBatch = 3
	}
	if c.RepeatNudgeBudget < 0 {
		c.RepeatNudgeBudget = 0
	} else if c.RepeatNudgeBudget == 0 {
		c.RepeatNudgeBudget = 2
	}
	if c.ModelRetries < 0 {
		c.ModelRetries = 0
	}
	return c
}

type TraceEvent struct {
	State     State
	Iteration int
	Detail    string
	At        time.Time
}
type Outcome struct {
	Text    string
	Results []ToolResult
	Trace   []TraceEvent
	Pending []ToolCall
	Err     error
}

type Engine struct {
	Model        Model
	Tools        ToolExecutor
	Policy       Policy
	Config       Config
	ApprovalGate ApprovalGate
	EpochHook    EpochHook
	Now          func() time.Time
}

func (e *Engine) Run(ctx context.Context, goal string) Outcome {
	return e.RunTurn(ctx, Turn{Goal: goal})
}

// RunTurn drives ReAct from a fully specified initial turn (goal + optional system).
func (e *Engine) RunTurn(ctx context.Context, seed Turn) Outcome {
	config := e.Config.normalized()
	now := e.Now
	if now == nil {
		now = time.Now
	}
	out := Outcome{}
	trace := func(state State, iteration int, detail string) {
		out.Trace = append(out.Trace, TraceEvent{State: state, Iteration: iteration, Detail: detail, At: now().UTC()})
	}
	var lastBatch string
	repeated := 0
	repeatNudges := 0
	toolCount := 0
	toolsThisEpoch := 0
	epoch := 0
	staleEpochs := 0
	goal := seed.Goal
	system := seed.System
	var prevCalls []ToolCall
	for iteration := 1; iteration <= config.MaxIterations; iteration++ {
		trace(StateObserve, iteration, "assemble turn")
		turn := Turn{
			Goal:      goal,
			System:    system,
			Text:      out.Text,
			Results:   append([]ToolResult(nil), out.Results...),
			Calls:     append([]ToolCall(nil), prevCalls...),
			Iteration: iteration,
		}
		trace(StateModel, iteration, "stream model")
		events, err := e.streamWithRetry(ctx, turn, config)
		if err != nil {
			out.Err = err
			trace(StateFailed, iteration, err.Error())
			return out
		}
		var calls []ToolCall
		completed := false
		for event := range events {
			out.Text += event.Text
			completed = completed || event.Done
			if event.ToolCall != nil {
				calls = append(calls, *event.ToolCall)
			}
		}
		if len(calls) == 0 {
			if !completed {
				out.Err = ErrIncompleteModelStream
				if ctx.Err() != nil {
					out.Err = ctx.Err()
				}
				trace(StateFailed, iteration, out.Err.Error())
				return out
			}
			trace(StateComplete, iteration, "model completed without tools")
			return out
		}
		toolCount += len(calls)
		toolsThisEpoch += len(calls)
		if toolCount > config.MaxToolCalls {
			// Soft-epoch builds extend on progress — never kill a productive mission.
			if config.SoftEpochSteps > 0 && toolsThisEpoch > 0 {
				config.MaxToolCalls = toolCount + config.SoftEpochSteps*config.MaxParallelTools
				trace(StateEpoch, iteration, fmt.Sprintf("extended tool ceiling to %d (progress)", config.MaxToolCalls))
			} else {
				out.Err = ErrToolCallLimit
				trace(StateFailed, iteration, out.Err.Error())
				return out
			}
		}
		batchKey := canonicalBatch(calls)
		if batchKey == lastBatch {
			repeated++
		} else {
			lastBatch, repeated = batchKey, 0
			repeatNudges = 0
		}
		if repeated >= config.MaxRepeatedBatch {
			if repeatNudges < config.RepeatNudgeBudget {
				repeatNudges++
				repeated = 0
				nudge := ToolResult{
					ID:   "epoch-nudge",
					Name: "remedy.nudge",
					Output: []byte(
						"Same tools repeated without progress. Change approach: " +
							"read a different path, edit the failing file, run a verify command, " +
							"or try a smaller next step. Do not restart the whole task.",
					),
				}
				out.Results = append(out.Results, nudge)
				trace(StateUpdate, iteration, "repeat-batch nudge")
				prevCalls = nil
				continue
			}
			out.Err = ErrNoProgress
			trace(StateFailed, iteration, out.Err.Error())
			return out
		}
		trace(StatePolicy, iteration, fmt.Sprintf("evaluate %d tools", len(calls)))
		allowed, pending, denied := e.partitionCalls(ctx, calls)
		if len(pending) > 0 {
			if e.ApprovalGate == nil {
				out.Pending = pending
				out.Err = ErrOwnerConfirmationNeeded
				out.Results = append(out.Results, denied...)
				trace(StatePaused, iteration, "owner checkpoint")
				return out
			}
			trace(StatePaused, iteration, "owner checkpoint")
			if err := e.ApprovalGate(ctx, pending); err != nil {
				out.Pending = pending
				out.Err = err
				if errors.Is(err, ErrOwnerDenied) {
					trace(StatePaused, iteration, "owner denied")
				} else {
					trace(StateFailed, iteration, err.Error())
				}
				return out
			}
			// Owner approved — fingerprints should now Allow. Re-decide once.
			allowed, pending, denied = e.partitionCalls(ctx, calls)
			if len(pending) > 0 {
				out.Pending = pending
				out.Err = ErrOwnerConfirmationNeeded
				out.Results = append(out.Results, denied...)
				trace(StatePaused, iteration, "still needs approval")
				return out
			}
		}
		trace(StateAct, iteration, fmt.Sprintf("execute %d tools", len(allowed)))
		// Replace observations with this batch only — OpenAI follow-ups pair
		// one assistant.tool_calls message with its matching tool results.
		out.Results = executeBatch(ctx, e.Tools, allowed, config.MaxParallelTools)
		if len(denied) > 0 {
			out.Results = append(out.Results, denied...)
		}
		prevCalls = append([]ToolCall(nil), allowed...)
		trace(StateUpdate, iteration, "append observations")

		// Soft epoch: compact context and continue — never a stop while tools work.
		if config.SoftEpochSteps > 0 && iteration%config.SoftEpochSteps == 0 {
			epoch++
			if toolsThisEpoch == 0 {
				staleEpochs++
				if staleEpochs >= config.MaxStaleEpochs {
					out.Err = ErrNoProgress
					trace(StateFailed, iteration, "stale epochs with no tool activity")
					return out
				}
			} else {
				staleEpochs = 0
				// Productive work extends the absolute runway so a real build
				// (days/weeks of tool rounds) never dies on a step counter.
				runway := config.SoftEpochSteps * 4
				if iteration+runway > config.MaxIterations {
					config.MaxIterations = iteration + runway
					trace(StateEpoch, iteration, fmt.Sprintf("extended step ceiling to %d (progress)", config.MaxIterations))
				}
				toolRunway := config.SoftEpochSteps * config.MaxParallelTools
				if toolCount+toolRunway > config.MaxToolCalls {
					config.MaxToolCalls = toolCount + toolRunway
				}
			}
			toolsThisEpoch = 0
			compactTurn(&out, &prevCalls, config.KeepLastResults, epoch, iteration)
			trace(StateEpoch, iteration, fmt.Sprintf("soft epoch %d continue", epoch))
			if e.EpochHook != nil {
				hookTurn := Turn{
					Goal:      goal,
					System:    system,
					Text:      out.Text,
					Results:   append([]ToolResult(nil), out.Results...),
					Calls:     append([]ToolCall(nil), prevCalls...),
					Iteration: iteration,
				}
				e.EpochHook(epoch, iteration, toolCount, &hookTurn)
				if hookTurn.Text != "" {
					out.Text = hookTurn.Text
				}
				if len(hookTurn.Results) > 0 {
					out.Results = hookTurn.Results
				}
			}
			continue
		}
		if iteration >= config.MaxIterations {
			break
		}
	}
	out.Err = ErrIterationLimit
	trace(StateFailed, config.MaxIterations, out.Err.Error())
	return out
}

func compactTurn(out *Outcome, prevCalls *[]ToolCall, keep int, epoch, totalSteps int) {
	if keep > 0 && len(out.Results) > keep {
		out.Results = append([]ToolResult(nil), out.Results[len(out.Results)-keep:]...)
	}
	// Drop prior assistant text accumulation so the next round starts lean;
	// tool results carry the working state.
	if len(out.Text) > 4000 {
		out.Text = out.Text[len(out.Text)-4000:]
	}
	nudge := fmt.Sprintf(
		"[Epoch %d complete at step %d] Context was compacted — this is not a stop. "+
			"Run until the task is finished: continue from the checkpoint / tool results above. "+
			"Do not restart, renumber, or summarize-only. Keep using tools until the work is done.",
		epoch, totalSteps,
	)
	out.Results = append(out.Results, ToolResult{
		ID:     fmt.Sprintf("epoch-%d", epoch),
		Name:   "remedy.epoch",
		Output: []byte(nudge),
	})
	*prevCalls = nil
}

func (e *Engine) partitionCalls(ctx context.Context, calls []ToolCall) (allowed, pending []ToolCall, denied []ToolResult) {
	for _, call := range calls {
		switch e.Policy.Decide(ctx, call) {
		case Allow:
			allowed = append(allowed, call)
		case Ask:
			pending = append(pending, call)
		case Deny:
			denied = append(denied, ToolResult{ID: call.ID, Name: call.Name, Err: "policy denied"})
		}
	}
	return allowed, pending, denied
}

func (e *Engine) streamWithRetry(ctx context.Context, turn Turn, config Config) (<-chan ModelEvent, error) {
	var err error
	for attempt := 0; attempt <= config.ModelRetries; attempt++ {
		var events <-chan ModelEvent
		events, err = e.Model.Stream(ctx, turn)
		if err == nil {
			return events, nil
		}
		if attempt < config.ModelRetries && !wait(ctx, config.RetryBackoff) {
			return nil, ctx.Err()
		}
	}
	return nil, err
}

func wait(ctx context.Context, d time.Duration) bool {
	if d <= 0 {
		return true
	}
	t := time.NewTimer(d)
	defer t.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-t.C:
		return true
	}
}

func canonicalBatch(calls []ToolCall) string {
	parts := make([]string, len(calls))
	for i, c := range calls {
		parts[i] = c.Name + "\x00" + string(c.Input)
	}
	sort.Strings(parts)
	return strings.Join(parts, "\x01")
}

func executeBatch(ctx context.Context, tools ToolExecutor, calls []ToolCall, maxParallel int) []ToolResult {
	if len(calls) == 0 {
		return nil
	}
	if maxParallel < 1 {
		maxParallel = 1
	}
	out := make([]ToolResult, len(calls))
	sem := make(chan struct{}, maxParallel)
	var wg sync.WaitGroup
	for i, call := range calls {
		wg.Add(1)
		sem <- struct{}{}
		go func(i int, call ToolCall) {
			defer wg.Done()
			defer func() { <-sem }()
			if tools == nil {
				out[i] = ToolResult{ID: call.ID, Name: call.Name, Err: "no tool executor"}
				return
			}
			out[i] = tools.Execute(ctx, call)
		}(i, call)
	}
	wg.Wait()
	return out
}
