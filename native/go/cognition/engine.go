// Package cognition implements Remedy's deterministic ReAct orchestration state machine.
package cognition

import (
	"context"
	"errors"
	"fmt"
	"sort"
	"sync"
	"time"
)

var (
	ErrIterationLimit          = errors.New("ReAct iteration limit reached")
	ErrToolCallLimit           = errors.New("ReAct tool-call limit reached")
	ErrNoProgress              = errors.New("ReAct made no progress")
	ErrOwnerConfirmationNeeded = errors.New("owner confirmation required")
)

type State string

const (
	StateObserve  State = "observe"
	StateModel    State = "model"
	StatePolicy   State = "policy"
	StateAct      State = "act"
	StateUpdate   State = "update"
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
	Text      string
	Results   []ToolResult
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

type Config struct {
	MaxIterations    int
	MaxToolCalls     int
	MaxParallelTools int
	MaxRepeatedBatch int
	ModelRetries     int
	RetryBackoff     time.Duration
}

func (c Config) normalized() Config {
	if c.MaxIterations <= 0 {
		c.MaxIterations = 24
	}
	if c.MaxToolCalls <= 0 {
		c.MaxToolCalls = 128
	}
	if c.MaxParallelTools <= 0 {
		c.MaxParallelTools = 8
	}
	if c.MaxRepeatedBatch <= 0 {
		c.MaxRepeatedBatch = 3
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
	Model  Model
	Tools  ToolExecutor
	Policy Policy
	Config Config
	Now    func() time.Time
}

func (e *Engine) Run(ctx context.Context, goal string) Outcome {
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
	toolCount := 0
	for iteration := 1; iteration <= config.MaxIterations; iteration++ {
		trace(StateObserve, iteration, "assemble turn")
		turn := Turn{Goal: goal, Text: out.Text, Results: append([]ToolResult(nil), out.Results...), Iteration: iteration}
		trace(StateModel, iteration, "stream model")
		events, err := e.streamWithRetry(ctx, turn, config)
		if err != nil {
			out.Err = err
			trace(StateFailed, iteration, err.Error())
			return out
		}
		var calls []ToolCall
		for event := range events {
			out.Text += event.Text
			if event.ToolCall != nil {
				calls = append(calls, *event.ToolCall)
			}
		}
		if len(calls) == 0 {
			trace(StateComplete, iteration, "model completed without tools")
			return out
		}
		toolCount += len(calls)
		if toolCount > config.MaxToolCalls {
			out.Err = ErrToolCallLimit
			trace(StateFailed, iteration, out.Err.Error())
			return out
		}
		batchKey := canonicalBatch(calls)
		if batchKey == lastBatch {
			repeated++
		} else {
			lastBatch, repeated = batchKey, 0
		}
		if repeated >= config.MaxRepeatedBatch {
			out.Err = ErrNoProgress
			trace(StateFailed, iteration, out.Err.Error())
			return out
		}
		trace(StatePolicy, iteration, fmt.Sprintf("evaluate %d tools", len(calls)))
		var allowed []ToolCall
		for _, call := range calls {
			switch e.Policy.Decide(ctx, call) {
			case Allow:
				allowed = append(allowed, call)
			case Ask:
				out.Pending = append(out.Pending, call)
				out.Err = ErrOwnerConfirmationNeeded
			case Deny:
				out.Results = append(out.Results, ToolResult{ID: call.ID, Name: call.Name, Err: "policy denied"})
			}
		}
		if len(out.Pending) > 0 {
			trace(StatePaused, iteration, "owner checkpoint")
			return out
		}
		trace(StateAct, iteration, fmt.Sprintf("execute %d tools", len(allowed)))
		out.Results = append(out.Results, executeBatch(ctx, e.Tools, allowed, config.MaxParallelTools)...)
		trace(StateUpdate, iteration, "append observations")
	}
	out.Err = ErrIterationLimit
	trace(StateFailed, config.MaxIterations, out.Err.Error())
	return out
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

func executeBatch(ctx context.Context, executor ToolExecutor, calls []ToolCall, limit int) []ToolResult {
	results := make([]ToolResult, len(calls))
	semaphore := make(chan struct{}, limit)
	var wg sync.WaitGroup
	for i, call := range calls {
		wg.Add(1)
		go func(i int, call ToolCall) {
			defer wg.Done()
			select {
			case semaphore <- struct{}{}:
				defer func() { <-semaphore }()
			case <-ctx.Done():
				results[i] = ToolResult{ID: call.ID, Name: call.Name, Err: ctx.Err().Error()}
				return
			}
			results[i] = executor.Execute(ctx, call)
		}(i, call)
	}
	wg.Wait()
	return results
}

func canonicalBatch(calls []ToolCall) string {
	parts := make([]string, len(calls))
	for i, call := range calls {
		parts[i] = call.Name + "\x00" + string(call.Input)
	}
	sort.Strings(parts)
	var result string
	for _, part := range parts {
		result += part + "\x01"
	}
	return result
}

func wait(ctx context.Context, duration time.Duration) bool {
	if duration <= 0 {
		return ctx.Err() == nil
	}
	timer := time.NewTimer(duration)
	defer timer.Stop()
	select {
	case <-ctx.Done():
		return false
	case <-timer.C:
		return true
	}
}
