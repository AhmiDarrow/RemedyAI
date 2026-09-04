package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"strings"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
)

// CognitionTurnRunner drives cognition.Engine and emits the @@ control tokens
// that stream.go already understands. No Python ReAct wrap — Go owns the loop.
type CognitionTurnRunner struct {
	Model  cognition.Model
	Tools  cognition.ToolExecutor
	Policy cognition.Policy
	Config cognition.Config
}

// NewCognitionTurnRunner builds a runner with Deny-default policy and echo tools
// when Tools/Policy are nil (safe until Tool ABI + real policy land).
func NewCognitionTurnRunner(model cognition.Model) *CognitionTurnRunner {
	return &CognitionTurnRunner{
		Model:  model,
		Tools:  cognition.EchoTools{},
		Policy: cognition.DenyAll{},
	}
}

func (r *CognitionTurnRunner) RunTurn(ctx context.Context, req TurnRequest, emit func(string) error) error {
	if r == nil || r.Model == nil {
		return errors.New("cognition turn runner requires a model")
	}
	tools := r.Tools
	if tools == nil {
		tools = cognition.EchoTools{}
	}
	policy := r.Policy
	if policy == nil {
		policy = cognition.DenyAll{}
	}

	var emitErr error
	safeEmit := func(tok string) {
		if emitErr != nil || emit == nil {
			return
		}
		if err := emit(tok); err != nil {
			emitErr = err
		}
	}

	engine := cognition.Engine{
		Model:  &emittingModel{inner: r.Model, emit: safeEmit},
		Tools:  &emittingTools{inner: tools, emit: safeEmit},
		Policy: policy,
		Config: r.Config,
	}
	out := engine.Run(ctx, req.Prompt)

	if emitErr != nil {
		return emitErr
	}
	if out.Err != nil {
		if errors.Is(out.Err, context.Canceled) || errors.Is(out.Err, context.DeadlineExceeded) {
			_ = emit("@@aborted\n")
			return nil
		}
		if errors.Is(out.Err, cognition.ErrOwnerConfirmationNeeded) {
			for _, call := range out.Pending {
				safeEmit(formatToolCallToken(call))
			}
			safeEmit("@@status:Waiting for your approval…\n")
			return nil
		}
		return out.Err
	}
	return nil
}

type emittingModel struct {
	inner cognition.Model
	emit  func(string)
}

func (m *emittingModel) Stream(ctx context.Context, turn cognition.Turn) (<-chan cognition.ModelEvent, error) {
	src, err := m.inner.Stream(ctx, turn)
	if err != nil {
		return nil, err
	}
	out := make(chan cognition.ModelEvent, 16)
	go func() {
		defer close(out)
		for {
			select {
			case <-ctx.Done():
				return
			case ev, ok := <-src:
				if !ok {
					return
				}
				if ev.Text != "" {
					m.emit(ev.Text)
				}
				if ev.ToolCall != nil {
					m.emit(formatToolCallToken(*ev.ToolCall))
				}
				select {
				case <-ctx.Done():
					return
				case out <- ev:
				}
			}
		}
	}()
	return out, nil
}

type emittingTools struct {
	inner cognition.ToolExecutor
	emit  func(string)
}

func (t *emittingTools) Execute(ctx context.Context, call cognition.ToolCall) cognition.ToolResult {
	res := t.inner.Execute(ctx, call)
	t.emit(formatToolResultToken(res))
	return res
}

func formatToolCallToken(call cognition.ToolCall) string {
	args := map[string]any{}
	if len(call.Input) > 0 {
		if err := json.Unmarshal(call.Input, &args); err != nil {
			args = map[string]any{"_raw": string(call.Input)}
		}
	}
	obj := map[string]any{"name": call.Name, "args": args}
	if call.ID != "" {
		obj["id"] = call.ID
	}
	b, err := json.Marshal(obj)
	if err != nil {
		return "@@tool_call:" + call.Name + "\n"
	}
	return "@@tool_call:" + string(b) + "\n"
}

func formatToolResultToken(res cognition.ToolResult) string {
	preview := string(res.Output)
	if len(preview) > 500 {
		preview = preview[:500] + "…"
	}
	ok := res.Err == ""
	obj := map[string]any{
		"name":    res.Name,
		"preview": preview,
		"ok":      ok,
	}
	if res.ID != "" {
		obj["id"] = res.ID
	}
	if !ok {
		obj["preview"] = res.Err
	}
	b, err := json.Marshal(obj)
	if err != nil {
		return "@@tool_result:" + res.Name + "\n"
	}
	return "@@tool_result:" + string(b) + "\n"
}

// CollectTokens runs a turn and returns concatenated emitted tokens (tests).
func CollectTokens(ctx context.Context, r TurnRunner, req TurnRequest) (string, error) {
	var b strings.Builder
	err := r.RunTurn(ctx, req, func(tok string) error {
		b.WriteString(tok)
		return nil
	})
	return b.String(), err
}
