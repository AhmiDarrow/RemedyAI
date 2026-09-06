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
	ErrIterationLimit          = errors.New("safety stop: step ceiling")
	ErrToolCallLimit           = errors.New("safety stop: tool ceiling")
	ErrBudgetExhausted         = errors.New("step budget exhausted")
	ErrNoProgress              = errors.New("stuck repeating without progress")
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
	// Truncated means finish_reason=length / max_tokens — continue, do not stop.
	Truncated bool
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
	// MaxIterations absolute safety ceiling on model rounds (default 1_000_000).
	MaxIterations int
	// MaxToolCalls absolute safety ceiling on tool invocations (default 10_000_000).
	MaxToolCalls int
	// SoftEpochSteps compact+continue every N model rounds (default 64).
	// Zero-value Config uses the default; set -1 to disable soft epochs.
	SoftEpochSteps int
	// MaxStaleEpochs stops after this many consecutive soft epochs with
	// zero tool activity (default 8). Failed tools still count as activity.
	MaxStaleEpochs int
	// KeepLastResults how many tool outcomes to fold into the epoch checkpoint (default 16).
	KeepLastResults int
	// MaxResultChars caps each tool output retained for the model (default 24_000).
	MaxResultChars int
	// MaxAssistantChars caps accumulated assistant text between rounds (default 2_000).
	MaxAssistantChars int
	// MaxLengthContinues caps finish_reason=length auto-continues (default 64).
	MaxLengthContinues int
	MaxParallelTools   int
	MaxRepeatedBatch   int
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
		// 64 keeps context lean for long builds (was 256 — too rare for efficiency).
		c.SoftEpochSteps = envInt("REMEDY_REACT_EPOCH_STEPS", 64, 16, 2_000)
	}
	if c.MaxStaleEpochs <= 0 {
		c.MaxStaleEpochs = envInt("REMEDY_REACT_MAX_STALE_EPOCHS", 8, 1, 50)
	}
	if c.KeepLastResults <= 0 {
		c.KeepLastResults = 16
	}
	if c.MaxResultChars <= 0 {
		c.MaxResultChars = envInt("REMEDY_REACT_MAX_RESULT_CHARS", 24_000, 2_000, 256_000)
	}
	if c.MaxAssistantChars <= 0 {
		c.MaxAssistantChars = envInt("REMEDY_REACT_MAX_ASSISTANT_CHARS", 2_000, 512, 32_000)
	}
	if c.MaxLengthContinues <= 0 {
		c.MaxLengthContinues = envInt("REMEDY_REACT_MAX_LENGTH_CONTINUES", 64, 1, 512)
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
	lengthContinues := 0
	toolCount := 0
	toolsThisEpoch := 0
	epoch := 0
	staleEpochs := 0
	goal := seed.Goal
	system := seed.System
	var prevCalls []ToolCall
	var outcomeLedger []string
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
		truncated := false
		for event := range events {
			out.Text += event.Text
			completed = completed || event.Done
			truncated = truncated || event.Truncated
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
			if truncated && lengthContinues < config.MaxLengthContinues {
				lengthContinues++
				out.Text = trimTail(out.Text, config.MaxAssistantChars)
				out.Text += "\n\n[Continue] Output hit the length limit — pick up exactly where you left off with tools. Do not restart or renumber."
				out.Results = nil
				prevCalls = nil
				trace(StateUpdate, iteration, "length auto-continue")
				continue
			}
			trace(StateComplete, iteration, "model completed without tools")
			return out
		}
		lengthContinues = 0
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
				// Fold nudge into assistant text — never orphan role=tool without Calls.
				out.Text = trimTail(out.Text, config.MaxAssistantChars)
				out.Text += "\n\n[Nudge] Same tools repeated without progress. Change approach: " +
					"read a different path, edit the failing file, run a verify command, " +
					"or try a smaller next step. Do not restart the whole task."
				out.Results = nil
				prevCalls = nil
				trace(StateUpdate, iteration, "repeat-batch nudge")
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
		out.Results = capResults(executeBatch(ctx, e.Tools, allowed, config.MaxParallelTools), config.MaxResultChars)
		if len(denied) > 0 {
			out.Results = append(out.Results, denied...)
		}
		prevCalls = append([]ToolCall(nil), allowed...)
		appendOutcomes(&outcomeLedger, out.Results, 24)
		// Bound assistant prose so every follow-up does not re-pay the monologue.
		out.Text = trimTail(out.Text, config.MaxAssistantChars)
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
			compactTurn(&out, &prevCalls, config.KeepLastResults, config.MaxResultChars, epoch, iteration, outcomeLedger)
			outcomeLedger = outcomeLedger[:0]
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

func trimTail(s string, max int) string {
	if max <= 0 || len(s) <= max {
		return s
	}
	return s[len(s)-max:]
}

func capResults(results []ToolResult, maxChars int) []ToolResult {
	if maxChars <= 0 || len(results) == 0 {
		return results
	}
	out := make([]ToolResult, len(results))
	for i, r := range results {
		out[i] = r
		out[i].Output = clipBytes(r.Output, maxChars)
	}
	return out
}

func clipBytes(b []byte, max int) []byte {
	if max <= 0 || len(b) <= max {
		return b
	}
	head := max * 2 / 3
	tail := max - head - 80
	if tail < 40 {
		tail = 40
		head = max - tail - 80
	}
	if head < 40 {
		return append([]byte(nil), b[:max]...)
	}
	msg := fmt.Sprintf("\n…[truncated %d chars — re-read the path or ask for an offset if you need more]…\n", len(b)-max)
	clipped := make([]byte, 0, max)
	clipped = append(clipped, b[:head]...)
	clipped = append(clipped, msg...)
	clipped = append(clipped, b[len(b)-tail:]...)
	return clipped
}

func appendOutcomes(ledger *[]string, results []ToolResult, max int) {
	for _, r := range results {
		line := outcomeLine(r)
		if line == "" {
			continue
		}
		*ledger = append(*ledger, line)
	}
	if max > 0 && len(*ledger) > max {
		*ledger = append([]string(nil), (*ledger)[len(*ledger)-max:]...)
	}
}

func outcomeLine(r ToolResult) string {
	name := strings.TrimSpace(r.Name)
	if name == "" {
		return ""
	}
	status := "ok"
	body := string(r.Output)
	if r.Err != "" {
		status = "err"
		body = r.Err
	}
	body = strings.ReplaceAll(body, "\n", " ")
	body = strings.TrimSpace(body)
	if len(body) > 160 {
		body = body[:160] + "…"
	}
	return fmt.Sprintf("- %s [%s] %s", name, status, body)
}

// compactTurn folds recent outcomes into assistant text and clears Calls/Results
// so the next provider round never sees orphan role=tool messages.
func compactTurn(out *Outcome, prevCalls *[]ToolCall, keep, maxResultChars, epoch, totalSteps int, ledger []string) {
	var b strings.Builder
	b.WriteString(fmt.Sprintf(
		"[Epoch %d @ step %d] Context compacted — not a stop. Continue with tools until the work is done. Do not restart.\n",
		epoch, totalSteps,
	))
	if len(ledger) > 0 {
		b.WriteString("Recent outcomes:\n")
		start := 0
		if keep > 0 && len(ledger) > keep {
			start = len(ledger) - keep
		}
		for _, line := range ledger[start:] {
			b.WriteString(line)
			b.WriteByte('\n')
		}
	} else if len(out.Results) > 0 {
		b.WriteString("Last batch:\n")
		for _, r := range capResults(out.Results, maxResultChars) {
			if line := outcomeLine(r); line != "" {
				b.WriteString(line)
				b.WriteByte('\n')
			}
		}
	}
	out.Text = trimTail(out.Text, 1200) + "\n\n" + b.String()
	out.Results = nil
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
