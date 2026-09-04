package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// CognitionTurnRunner drives cognition.Engine and emits the @@ control tokens
// that stream.go already understands. No Python ReAct wrap — Go owns the loop.
type CognitionTurnRunner struct {
	Model    cognition.Model
	Tools    cognition.ToolExecutor
	Policy   cognition.Policy
	Config   cognition.Config
	Registry *tools.Registry
}

// NewCognitionTurnRunner builds a runner on the real Tool ABI registry (Go
// builtins in-process). Pass AttachPythonWorker to add RuntimePython tools over
// RMDY frames. Missing Tools/Policy is an error — no Echo/DenyAll fallback.
func NewCognitionTurnRunner(model cognition.Model) *CognitionTurnRunner {
	registry, err := NewDefaultToolRegistry(nil)
	if err != nil {
		panic("tool ABI builtins failed to register: " + err.Error())
	}
	r := &CognitionTurnRunner{
		Model:    model,
		Registry: registry,
		Tools:    &RegistryToolExecutor{Registry: registry, TokenFor: RuntimeCapabilityToken},
		Policy:   &RegistryPolicy{Registry: registry},
	}
	r.syncModelToolSchemas()
	return r
}

// AttachPythonWorker registers RuntimePython tools that execute over RMDY frames.
func (r *CognitionTurnRunner) AttachPythonWorker(caller tools.FrameCaller) error {
	if r == nil || r.Registry == nil {
		return errors.New("cognition turn runner has no tool registry")
	}
	if err := tools.RegisterPythonWorkerTools(r.Registry, caller); err != nil {
		return err
	}
	r.Tools = &RegistryToolExecutor{Registry: r.Registry, TokenFor: RuntimeCapabilityToken}
	r.Policy = &RegistryPolicy{Registry: r.Registry}
	r.syncModelToolSchemas()
	return nil
}

// syncModelToolSchemas advertises the Tool ABI surface on OpenAI-compatible requests.
func (r *CognitionTurnRunner) syncModelToolSchemas() {
	if r == nil || r.Registry == nil {
		return
	}
	oc, ok := r.Model.(*providers.OpenAICompat)
	if !ok || oc == nil {
		return
	}
	list := r.Registry.List()
	meta := make([]providers.RegistryTool, 0, len(list))
	for _, d := range list {
		meta = append(meta, providers.RegistryTool{
			ID:          d.ID,
			Description: d.Description,
			InputSchema: append(json.RawMessage(nil), d.InputSchema...),
		})
	}
	oc.Tools = providers.ToolSchemasFromRegistry(meta)
}

func (r *CognitionTurnRunner) RunTurn(ctx context.Context, req TurnRequest, emit func(string) error) error {
	if r == nil || r.Model == nil {
		return errors.New("cognition turn runner requires a model")
	}
	if r.Tools == nil {
		return errors.New("cognition turn runner requires a tool executor")
	}
	if r.Policy == nil {
		return errors.New("cognition turn runner requires a policy")
	}
	execTools := r.Tools
	policy := r.Policy

	var (
		emitMu  sync.Mutex
		emitErr error
	)
	// Tool batches run concurrently; serialize emit + emitErr.
	safeEmit := func(tok string) {
		emitMu.Lock()
		defer emitMu.Unlock()
		if emitErr != nil || emit == nil {
			return
		}
		if err := emit(tok); err != nil {
			emitErr = err
		}
	}

	engine := cognition.Engine{
		Model:  &emittingModel{inner: r.Model, emit: safeEmit},
		Tools:  &emittingTools{inner: execTools, emit: safeEmit},
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
	var (
		mu sync.Mutex
		b  strings.Builder
	)
	err := r.RunTurn(ctx, req, func(tok string) error {
		mu.Lock()
		defer mu.Unlock()
		b.WriteString(tok)
		return nil
	})
	mu.Lock()
	out := b.String()
	mu.Unlock()
	return out, err
}
