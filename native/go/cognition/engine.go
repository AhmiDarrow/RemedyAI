// Package cognition implements Remedy's deterministic ReAct orchestration state machine.
package cognition

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"os"
	"sort"
	"strconv"
	"strings"
	"sync"
	"time"
	"unicode/utf8"
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
	// Name is the Tool ABI id (e.g. "shell.exec") once the runner has resolved it.
	ID, Name string
	// Advertised is the provider-facing function name the model actually
	// emitted (e.g. "shell_exec"), recorded for the turn log and for adapters
	// that echo it. Policy, approvals and the executor only ever see Name; the
	// OpenAI adapter maps ABI ids back to wire names through its own tool map.
	Advertised string
	Input      []byte
}

type ToolResult struct {
	ID, Name string
	Output   []byte
	Err      string
	// Blocks carries non-text results (screenshots) back into the transcript.
	Blocks []Block
	// IsError marks a result the model must read as a failure even when the
	// executor returned a body instead of Err.
	IsError bool
}

// Usage is the provider-reported token count for one model round. The cache
// counts are Anthropic's prompt-cache accounting and are omitted by providers
// that do not report them.
type Usage struct {
	PromptTokens     int `json:"prompt_tokens"`
	CompletionTokens int `json:"completion_tokens"`
	TotalTokens      int `json:"total_tokens"`
	// CacheReadTokens is prompt input served from the provider's cache.
	CacheReadTokens int `json:"cache_read_tokens,omitempty"`
	// CacheWriteTokens is prompt input written into the cache this round.
	CacheWriteTokens int `json:"cache_write_tokens,omitempty"`
}

type ModelEvent struct {
	Text string
	// Thinking is a reasoning delta (reasoning_content / summarized thinking).
	Thinking string
	// Status is an out-of-band notice about the round (a refusal category, a
	// context-window stop). It is never part of the assistant's answer.
	Status   string
	ToolCall *ToolCall
	Usage    *Usage
	Done     bool
	// Truncated means finish_reason=length / max_tokens — continue, do not stop.
	Truncated bool
}

// Model is one provider binding. ContextWindow reports the prompt window in
// tokens (0 when the adapter cannot know it; the engine then assumes
// DefaultContextWindow).
type Model interface {
	Stream(context.Context, Turn) (<-chan ModelEvent, error)
	ContextWindow() int
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

// EpochHook runs at soft-epoch boundaries. Soft epochs never stop the turn and
// never trim the transcript — they exist so the Session Brief can be refreshed
// and a checkpoint status emitted. ledger holds the outcome lines of the epoch
// that just closed. Only a non-empty turn.System is applied when it returns.
type EpochHook func(epoch, totalSteps, toolCalls int, ledger []string, turn *Turn)

// ContinueGate decides whether a text-only model completion should re-arm
// tools (unfinished build/mission/agency promise). lastResults is the most
// recent tool batch; verifySeen reports whether a verify command has passed
// since the last mutation. Return continue=true and an optional nudge string.
type ContinueGate func(ctx context.Context, turn Turn, toolCount int, lastResults []ToolResult, verifySeen bool) (shouldContinue bool, nudge string)

// Config controls ReAct pacing. Soft epochs refresh the brief and continue;
// absolute ceilings exist only as pathological-loop safety nets — not task
// budgets. Same operating model as a Build agent: run until the request is
// finished.
type Config struct {
	// MaxIterations absolute safety ceiling on model rounds (default 1_000_000).
	MaxIterations int
	// MaxToolCalls absolute safety ceiling on tool invocations (default 10_000_000).
	MaxToolCalls int
	// SoftEpochSteps refresh the brief every N model rounds (default 64).
	// Zero-value Config uses the default; set -1 to disable soft epochs.
	SoftEpochSteps int
	// MaxStaleEpochs stops after this many consecutive soft epochs with
	// zero tool activity (default 8). Failed tools still count as activity.
	MaxStaleEpochs int
	// KeepLastResults how many outcome lines the epoch ledger keeps (default 32).
	KeepLastResults int
	// MaxResultChars caps each tool output retained for the model (default 24_000).
	MaxResultChars int
	// MaxLengthContinues caps finish_reason=length auto-continues (default 64).
	MaxLengthContinues int
	// MaxRearms caps unfinished-work / agency re-arms after text-only stops (default 8).
	MaxRearms        int
	MaxParallelTools int
	// MaxRepeatedBatch is how many consecutive identical batches trigger the
	// progress gate (default 2 — i.e. the third identical batch).
	MaxRepeatedBatch int
	// RepeatNudgeBudget allows that many nudges before ErrNoProgress (default 1).
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
		c.SoftEpochSteps = envInt("REMEDY_REACT_EPOCH_STEPS", 64, 16, 2_000)
	}
	if c.MaxStaleEpochs <= 0 {
		c.MaxStaleEpochs = envInt("REMEDY_REACT_MAX_STALE_EPOCHS", 8, 1, 50)
	}
	if c.KeepLastResults <= 0 {
		c.KeepLastResults = 32
	}
	if c.MaxResultChars <= 0 {
		c.MaxResultChars = envInt("REMEDY_REACT_MAX_RESULT_CHARS", 24_000, 2_000, 256_000)
	}
	if c.MaxLengthContinues <= 0 {
		c.MaxLengthContinues = envInt("REMEDY_REACT_MAX_LENGTH_CONTINUES", 64, 1, 512)
	}
	if c.MaxRearms <= 0 {
		c.MaxRearms = envInt("REMEDY_REACT_MAX_REARMS", 8, 1, 64)
	}
	if c.MaxParallelTools <= 0 {
		c.MaxParallelTools = envInt("REMEDY_MAX_PARALLEL_TOOLS", 32, 4, 64)
	}
	if c.MaxRepeatedBatch <= 0 {
		c.MaxRepeatedBatch = 2
	}
	if c.RepeatNudgeBudget < 0 {
		c.RepeatNudgeBudget = 0
	} else if c.RepeatNudgeBudget == 0 {
		c.RepeatNudgeBudget = 1
	}
	if c.ModelRetries < 0 {
		c.ModelRetries = 0
	}
	return c
}

// ToolKind classifies a tool for progress / evidence gating.
type ToolKind uint8

const (
	ToolExplore ToolKind = iota
	ToolMutate
	ToolVerify
)

func ClassifyTool(name string) ToolKind {
	n := strings.ToLower(strings.TrimSpace(name))
	n = strings.ReplaceAll(n, "-", "_")
	n = strings.ReplaceAll(n, ".", "_")
	switch n {
	case "workspace_edit", "workspace_write", "workspace_create", "workspace_delete",
		"workspace_rename", "workspace_mkdir", "file_edit", "file_write", "file_delete",
		"apply_patch", "str_replace", "search_replace", "write", "edit",
		// Computer-use input changes the world even though it reads nothing back.
		"computer_click", "computer_type", "computer_key", "computer_key_hold",
		"computer_scroll", "computer_drag", "computer_focus", "computer_window",
		"computer_navigate", "computer_uia_action", "clipboard_write":
		return ToolMutate
	case "shell_exec", "bash", "bash_exec", "host_run", "job_run", "process_spawn",
		"mission_verify", "build_drive", "pytest", "cargo_test":
		return ToolVerify
	case "workspace_read", "workspace_list", "workspace_search", "workspace_grep",
		"file_read", "read", "grep", "glob", "list_dir", "search", "web_search",
		"memory_search", "skill_search", "codebase_search":
		return ToolExplore
	default:
		if strings.Contains(n, "write") || strings.Contains(n, "edit") ||
			strings.Contains(n, "delete") || strings.Contains(n, "patch") ||
			strings.Contains(n, "create") {
			return ToolMutate
		}
		if strings.Contains(n, "verify") || strings.Contains(n, "test") ||
			strings.Contains(n, "shell") || strings.Contains(n, "exec") {
			return ToolVerify
		}
		return ToolExplore
	}
}

func batchHasProductive(calls []ToolCall) bool {
	for _, c := range calls {
		switch ClassifyTool(c.Name) {
		case ToolMutate, ToolVerify:
			return true
		}
	}
	return false
}

type TraceEvent struct {
	State     State
	Iteration int
	Detail    string
	At        time.Time
}

type Outcome struct {
	// Text is everything the model said across this turn's rounds.
	Text string
	// Results is the most recent executed tool batch.
	Results []ToolResult
	// Messages is the transcript as the model last saw it, including the final
	// assistant message.
	Messages []Message
	Trace    []TraceEvent
	Pending  []ToolCall
	Err      error
}

type Engine struct {
	Model        Model
	Tools        ToolExecutor
	Policy       Policy
	Config       Config
	ApprovalGate ApprovalGate
	EpochHook    EpochHook
	ContinueGate ContinueGate
	Now          func() time.Time
}

// Run drives a turn from a plain goal string (one user message).
func (e *Engine) Run(ctx context.Context, goal string) Outcome {
	return e.RunTurn(ctx, Turn{Messages: []Message{UserText(goal)}})
}

const (
	// DefaultContextWindow is the assumed prompt window when an adapter cannot
	// report one (every current frontier cloud model is at least this large).
	DefaultContextWindow = 200_000
	// reservedOutputTokens is held back from the window for the reply.
	reservedOutputTokens = 16_000
	// promptBudgetFraction of the window may be spent on the transcript.
	promptBudgetFraction = 0.75
)

// transcriptBudget is the token ceiling the transcript may reach before the
// engine compacts it.
func transcriptBudget(window int) int {
	if window <= 0 {
		window = DefaultContextWindow
	}
	budget := int(float64(window)*promptBudgetFraction) - reservedOutputTokens
	if floor := window / 4; budget < floor {
		budget = floor
	}
	if budget < 512 {
		budget = 512
	}
	return budget
}

// RunTurn drives ReAct over an append-only transcript. seed.Messages is the
// conversation so far (history + the current user message with attachments);
// every model round and every tool batch is appended, never replaced.
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
	transcript := cloneMessages(seed.Messages)
	if len(transcript) == 0 {
		transcript = []Message{UserText("continue")}
	}
	system := seed.System
	budget := transcriptBudget(e.contextWindow())

	var lastBatch string
	repeated := 0
	repeatNudges := 0
	lengthContinues := 0
	rearmCount := 0
	toolCount := 0
	toolsThisEpoch := 0
	productiveThisEpoch := 0
	epoch := 0
	staleEpochs := 0
	verifySeen := true
	var outcomeLedger []string

	for iteration := 1; iteration <= config.MaxIterations; iteration++ {
		trace(StateObserve, iteration, "assemble transcript")
		if est := estimateTokens(transcript); est > budget {
			before := len(transcript)
			transcript = compactTranscript(transcript)
			trace(StateUpdate, iteration, fmt.Sprintf(
				"compacted transcript %d→%d messages (~%d tokens over %d)", before, len(transcript), est, budget))
		}
		turn := Turn{System: system, Messages: cloneMessages(transcript), Iteration: iteration}
		out.Messages = turn.Messages

		trace(StateModel, iteration, "stream model")
		events, err := e.streamWithRetry(ctx, turn, config)
		if err != nil {
			out.Err = err
			trace(StateFailed, iteration, err.Error())
			return out
		}
		var text, thinking strings.Builder
		var calls []ToolCall
		completed := false
		truncated := false
		for event := range events {
			text.WriteString(event.Text)
			thinking.WriteString(event.Thinking)
			completed = completed || event.Done
			truncated = truncated || event.Truncated
			if event.ToolCall != nil {
				calls = append(calls, *event.ToolCall)
			}
		}
		if err := ctx.Err(); err != nil {
			out.Err = err
			trace(StateFailed, iteration, err.Error())
			return out
		}
		roundText := text.String()
		out.Text += roundText
		// Every tool_use block must carry an id its result can be paired to;
		// providers that omit one get a stable synthetic id here.
		for i := range calls {
			if strings.TrimSpace(calls[i].ID) == "" {
				calls[i].ID = fmt.Sprintf("call_%d_%d", iteration, i+1)
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
			transcript = appendAssistant(transcript, thinking.String(), roundText, nil)
			if truncated && lengthContinues < config.MaxLengthContinues {
				lengthContinues++
				transcript = append(transcript, UserText(
					"[Continue] Your output hit the length limit — pick up exactly where you left off. "+
						"Do not restart, do not renumber, do not repeat what you already wrote."))
				trace(StateUpdate, iteration, "length auto-continue")
				continue
			}
			// Unfinished-work / agency re-arm — production sets ContinueGate.
			// Nil gate preserves plain text-only completion (tests / fixtures).
			if e.ContinueGate != nil && rearmCount < config.MaxRearms {
				gateTurn := Turn{System: system, Messages: cloneMessages(transcript), Iteration: iteration}
				should, nudge := e.ContinueGate(ctx, gateTurn, toolCount, append([]ToolResult(nil), out.Results...), verifySeen)
				if should {
					rearmCount++
					if strings.TrimSpace(nudge) == "" {
						nudge = "Work is unfinished — call tools now via the function-calling API. Do not narrate; execute."
					}
					transcript = append(transcript, UserText("[Re-arm] "+strings.TrimSpace(nudge)))
					trace(StateUpdate, iteration, fmt.Sprintf("unfinished re-arm %d", rearmCount))
					continue
				}
			}
			out.Messages = cloneMessages(transcript)
			trace(StateComplete, iteration, "model completed without tools")
			return out
		}

		lengthContinues = 0
		toolCount += len(calls)
		toolsThisEpoch += len(calls)
		productive := batchHasProductive(calls)
		if productive {
			productiveThisEpoch += len(calls)
		}
		if toolCount > config.MaxToolCalls {
			// Soft-epoch builds extend on productive progress only — explore thrash
			// must not raise the ceiling (coherent endless builds, not endless scouting).
			if config.SoftEpochSteps > 0 && productiveThisEpoch > 0 {
				config.MaxToolCalls = toolCount + config.SoftEpochSteps*config.MaxParallelTools
				trace(StateEpoch, iteration, fmt.Sprintf("extended tool ceiling to %d (productive progress)", config.MaxToolCalls))
			} else {
				transcript = appendAssistant(transcript, thinking.String(), roundText, calls)
				out.Messages = cloneMessages(transcript)
				out.Err = ErrToolCallLimit
				trace(StateFailed, iteration, out.Err.Error())
				return out
			}
		}

		// Novelty gate: an identical batch (sorted ABI name + canonical input)
		// three times gets one nudge; a fourth ends the turn.
		batchKey := noveltyKey(calls)
		if batchKey == lastBatch {
			repeated++
		} else {
			lastBatch, repeated, repeatNudges = batchKey, 0, 0
		}
		if repeated >= config.MaxRepeatedBatch {
			if repeatNudges < config.RepeatNudgeBudget {
				repeatNudges++
				// The refused batch never runs, so its tool_use blocks stay out
				// of the transcript (they would be unanswered tool calls); the
				// nudge names them instead.
				transcript = appendAssistant(transcript, thinking.String(), roundText, nil)
				transcript = append(transcript, UserText(
					"[Nudge] You just asked for the same tools again with the same arguments ("+
						batchPreview(calls)+") and nothing changed. That batch was not run. "+
						"Change approach: read a different path, edit the failing file, run a verify "+
						"command, or take a smaller next step. Do not restart the task."))
				trace(StateUpdate, iteration, "repeat-batch nudge")
				continue
			}
			transcript = appendAssistant(transcript, thinking.String(), roundText, calls)
			out.Messages = cloneMessages(transcript)
			out.Err = ErrNoProgress
			trace(StateFailed, iteration, out.Err.Error())
			return out
		}

		transcript = appendAssistant(transcript, thinking.String(), roundText, calls)

		trace(StatePolicy, iteration, fmt.Sprintf("evaluate %d tools", len(calls)))
		decisions := e.decide(ctx, calls)
		for gateAttempt := 0; ; gateAttempt++ {
			pending := callsWith(calls, decisions, Ask)
			if len(pending) == 0 {
				break
			}
			if e.ApprovalGate == nil || gateAttempt >= 2 {
				out.Pending = pending
				out.Err = ErrOwnerConfirmationNeeded
				out.Messages = cloneMessages(transcript)
				trace(StatePaused, iteration, "owner checkpoint")
				return out
			}
			trace(StatePaused, iteration, "owner checkpoint")
			if err := e.ApprovalGate(ctx, pending); err != nil {
				out.Pending = pending
				out.Err = err
				out.Messages = cloneMessages(transcript)
				if errors.Is(err, ErrOwnerDenied) {
					trace(StatePaused, iteration, "owner denied")
				} else {
					trace(StateFailed, iteration, err.Error())
				}
				return out
			}
			decisions = e.decide(ctx, calls)
		}

		allowed := callsWith(calls, decisions, Allow)
		trace(StateAct, iteration, fmt.Sprintf("execute %d tools", len(allowed)))
		results := capResults(e.executeDecided(ctx, calls, decisions, config.MaxParallelTools), config.MaxResultChars)
		out.Results = results

		// One user message carrying every result in call order — never dropped.
		blocks := make([]Block, 0, len(calls))
		for i, call := range calls {
			blocks = append(blocks, resultBlocks(call, results[i]))
		}
		transcript = append(transcript, Message{Role: RoleUser, Blocks: blocks})
		appendOutcomes(&outcomeLedger, results, config.KeepLastResults)

		// Evidence: a mutation invalidates the last green verify; a passing
		// verify restores it. The ContinueGate reads this at completion time.
		for i, call := range calls {
			switch ClassifyTool(call.Name) {
			case ToolMutate:
				if results[i].Err == "" && !results[i].IsError {
					verifySeen = false
				}
			case ToolVerify:
				if resultLooksGreen(results[i]) {
					verifySeen = true
				}
			}
		}
		trace(StateUpdate, iteration, fmt.Sprintf("append %d tool results", len(results)))

		// Soft epoch: refresh the Session Brief and keep going. It never trims
		// the transcript — the budget check at the top of the loop owns that.
		if config.SoftEpochSteps > 0 && iteration%config.SoftEpochSteps == 0 {
			epoch++
			if toolsThisEpoch == 0 {
				staleEpochs++
				if staleEpochs >= config.MaxStaleEpochs {
					out.Err = ErrNoProgress
					out.Messages = cloneMessages(transcript)
					trace(StateFailed, iteration, "stale epochs with no tool activity")
					return out
				}
			} else if productiveThisEpoch > 0 {
				staleEpochs = 0
				// Productive mutate/verify extends the absolute runway so a real
				// build never dies on a step counter. Explore-only does not.
				runway := config.SoftEpochSteps * 4
				if iteration+runway > config.MaxIterations {
					config.MaxIterations = iteration + runway
					trace(StateEpoch, iteration, fmt.Sprintf("extended step ceiling to %d (productive progress)", config.MaxIterations))
				}
				toolRunway := config.SoftEpochSteps * config.MaxParallelTools
				if toolCount+toolRunway > config.MaxToolCalls {
					config.MaxToolCalls = toolCount + toolRunway
				}
			} else {
				staleEpochs = 0
				trace(StateEpoch, iteration, "explore-only epoch (no runway extend)")
			}
			toolsThisEpoch = 0
			productiveThisEpoch = 0
			ledger := append([]string(nil), outcomeLedger...)
			outcomeLedger = outcomeLedger[:0]
			trace(StateEpoch, iteration, fmt.Sprintf("soft epoch %d continue", epoch))
			if e.EpochHook != nil {
				hookTurn := Turn{System: system, Messages: cloneMessages(transcript), Iteration: iteration}
				e.EpochHook(epoch, iteration, toolCount, ledger, &hookTurn)
				if strings.TrimSpace(hookTurn.System) != "" {
					system = hookTurn.System
				}
			}
			continue
		}
		if iteration >= config.MaxIterations {
			break
		}
	}
	out.Messages = cloneMessages(transcript)
	out.Err = ErrIterationLimit
	trace(StateFailed, config.MaxIterations, out.Err.Error())
	return out
}

func (e *Engine) contextWindow() int {
	if e.Model == nil {
		return 0
	}
	return e.Model.ContextWindow()
}

// appendAssistant adds one assistant message for a model round: thinking, then
// prose, then the tool_use blocks that will be executed.
func appendAssistant(transcript []Message, thinking, text string, calls []ToolCall) []Message {
	blocks := make([]Block, 0, 2+len(calls))
	if strings.TrimSpace(thinking) != "" {
		blocks = append(blocks, ThinkingBlock(thinking))
	}
	if text != "" {
		blocks = append(blocks, TextBlock(text))
	}
	for _, call := range calls {
		blocks = append(blocks, ToolUseBlock(call))
	}
	if len(blocks) == 0 {
		return transcript
	}
	return append(transcript, Message{Role: RoleAssistant, Blocks: blocks})
}

// batchPreview names the tools of a refused batch for the nudge line.
func batchPreview(calls []ToolCall) string {
	parts := make([]string, 0, len(calls))
	for _, call := range calls {
		parts = append(parts, call.Name+" "+clipRunes(strings.TrimSpace(string(call.Input)), 120))
	}
	return strings.Join(parts, ", ")
}

// runeFloor returns the largest index <= i that starts a rune in b.
func runeFloor(b []byte, i int) int {
	for i > 0 && i < len(b) && !utf8.RuneStart(b[i]) {
		i--
	}
	return i
}

// runeCeil returns the smallest index >= i that starts a rune in b (or len(b)).
func runeCeil(b []byte, i int) int {
	for i < len(b) && !utf8.RuneStart(b[i]) {
		i++
	}
	return i
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
		return append([]byte(nil), b[:runeFloor(b, max)]...)
	}
	head = runeFloor(b, head)
	tailStart := runeCeil(b, len(b)-tail)
	msg := fmt.Sprintf("\n…[truncated %d chars — re-read the path or ask for an offset if you need more]…\n", len(b)-max)
	clipped := make([]byte, 0, max)
	clipped = append(clipped, b[:head]...)
	clipped = append(clipped, msg...)
	clipped = append(clipped, b[tailStart:]...)
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
	} else if r.IsError {
		status = "err"
	}
	body = strings.ReplaceAll(body, "\n", " ")
	body = strings.TrimSpace(body)
	if len(body) > 160 {
		body = body[:runeFloor([]byte(body), 160)] + "…"
	}
	return fmt.Sprintf("- %s [%s] %s", name, status, body)
}

// decide asks policy once per call, in call order.
func (e *Engine) decide(ctx context.Context, calls []ToolCall) []Decision {
	out := make([]Decision, len(calls))
	for i, call := range calls {
		if e.Policy == nil {
			out[i] = Deny
			continue
		}
		out[i] = e.Policy.Decide(ctx, call)
	}
	return out
}

func callsWith(calls []ToolCall, decisions []Decision, want Decision) []ToolCall {
	var out []ToolCall
	for i, call := range calls {
		if decisions[i] == want {
			out = append(out, call)
		}
	}
	return out
}

// executeDecided runs the allowed calls in parallel and returns one result per
// call in call order; denied calls get an error result instead.
func (e *Engine) executeDecided(ctx context.Context, calls []ToolCall, decisions []Decision, maxParallel int) []ToolResult {
	results := make([]ToolResult, len(calls))
	if maxParallel < 1 {
		maxParallel = 1
	}
	sem := make(chan struct{}, maxParallel)
	var wg sync.WaitGroup
	for i, call := range calls {
		if decisions[i] != Allow {
			results[i] = ToolResult{ID: call.ID, Name: call.Name, Err: "policy denied", IsError: true}
			continue
		}
		wg.Add(1)
		sem <- struct{}{}
		go func(i int, call ToolCall) {
			defer wg.Done()
			defer func() { <-sem }()
			if e.Tools == nil {
				results[i] = ToolResult{ID: call.ID, Name: call.Name, Err: "no tool executor", IsError: true}
				return
			}
			results[i] = e.Tools.Execute(ctx, call)
		}(i, call)
	}
	wg.Wait()
	return results
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

// noveltyKey identifies a tool batch by sorted (ABI name, canonical input), so
// re-ordered or re-keyed arguments do not read as a new attempt.
func noveltyKey(calls []ToolCall) string {
	parts := make([]string, len(calls))
	for i, c := range calls {
		parts[i] = c.Name + "\x00" + canonicalInput(c.Input)
	}
	sort.Strings(parts)
	return strings.Join(parts, "\x01")
}

// canonicalInput re-encodes a JSON tool input with sorted object keys; input
// that is not JSON is compared verbatim.
func canonicalInput(raw []byte) string {
	if len(raw) == 0 {
		return ""
	}
	var decoded any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		return string(raw)
	}
	canonical, err := json.Marshal(decoded)
	if err != nil {
		return string(raw)
	}
	return string(canonical)
}
