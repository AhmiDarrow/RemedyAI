package httpapi

import (
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"strings"
	"sync"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/providers"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// CognitionTurnRunner drives cognition.Engine and emits the @@ control tokens
// that stream.go already understands. No Python ReAct wrap — Go owns the loop.
type CognitionTurnRunner struct {
	Model cognition.Model
	// HomeDir enables per-turn ResolveChatModel (xAI OAuth, vision helper).
	// When empty, RunTurn uses Model as-is (unit tests / fixtures).
	HomeDir string
	// forcePrimary, when set, is the first model attempt (tests). Production
	// leaves it nil so modelForTurn resolves from HomeDir / Model.
	forcePrimary   cognition.Model
	Tools          cognition.ToolExecutor
	Policy         cognition.Policy
	Config         cognition.Config
	Registry       *tools.Registry
	promptAssemble *tools.RMDYExecutor
}

// NewCognitionTurnRunner builds a runner on the real Tool ABI registry (Go
// builtins + RuntimeZig host tools in-process). Pass AttachPythonWorker to add
// RuntimePython tools over RMDY frames. Missing Tools/Policy is an error —
// no Echo/DenyAll fallback. Zig-owned tools fail closed when remedy_core is
// unavailable (no Python soft fallback).
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
// Also enables prompt.assemble before each model turn (fail closed on error).
func (r *CognitionTurnRunner) AttachPythonWorker(caller tools.FrameCaller) error {
	if r == nil || r.Registry == nil {
		return errors.New("cognition turn runner has no tool registry")
	}
	if caller == nil {
		return errors.New("python worker frame caller is required")
	}
	if err := tools.RegisterPythonWorkerTools(r.Registry, caller); err != nil {
		return err
	}
	r.promptAssemble = tools.NewRMDYExecutor(caller)
	r.Tools = &RegistryToolExecutor{Registry: r.Registry, TokenFor: RuntimeCapabilityToken}
	r.Policy = &RegistryPolicy{Registry: r.Registry}
	r.syncModelToolSchemas()
	return nil
}

// modelVisibleTool reports whether a Tool ABI id should be advertised to the LLM.
// Internal services (prompt.*/voice.*/vision.*) stay off the model surface.
func modelVisibleTool(id string) bool {
	return !strings.HasPrefix(id, "prompt.") &&
		!strings.HasPrefix(id, "voice.") &&
		!strings.HasPrefix(id, "vision.")
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
	r.applyToolSchemas(oc)
}

func (r *CognitionTurnRunner) applyToolSchemas(oc *providers.OpenAICompat) {
	if r == nil || r.Registry == nil || oc == nil {
		return
	}
	// Local vision helper is for basic chat only — do not advertise the full
	// tool surface to a 2B VLM.
	if isVisionHelperCompat(oc) {
		oc.Tools = nil
		return
	}
	list := r.Registry.List()
	meta := make([]providers.RegistryTool, 0, len(list))
	for _, d := range list {
		if !modelVisibleTool(d.ID) {
			continue
		}
		meta = append(meta, providers.RegistryTool{
			ID:          d.ID,
			Description: d.Description,
			InputSchema: append(json.RawMessage(nil), d.InputSchema...),
		})
	}
	schemas, nameMap := providers.ToolSchemasFromRegistryMapped(meta)
	oc.Tools = schemas
	oc.ToolNameMap = nameMap
}

func isVisionHelperCompat(oc *providers.OpenAICompat) bool {
	if oc == nil {
		return false
	}
	if strings.EqualFold(strings.TrimSpace(oc.Model), "vision-decoder") {
		return true
	}
	base := strings.TrimSpace(oc.BaseURL)
	return isLocalURL(base) && strings.Contains(base, fmt.Sprintf(":%d", visionDefaultPort))
}

func (r *CognitionTurnRunner) modelForTurn(req TurnRequest) cognition.Model {
	if r == nil {
		return nil
	}
	if r.forcePrimary != nil {
		return r.forcePrimary
	}
	if strings.TrimSpace(r.HomeDir) != "" || r.Model == nil {
		home := r.HomeDir
		prov, model := "", ""
		if req.Provider != nil {
			prov = strings.TrimSpace(*req.Provider)
		}
		if req.Model != nil {
			model = strings.TrimSpace(*req.Model)
		}
		live := ResolveChatModel(home, prov, model, "")
		if oc, ok := live.(*providers.OpenAICompat); ok {
			r.applyToolSchemas(oc)
		}
		return live
	}
	return r.Model
}

type assembledPrompt struct {
	System string
	Goal   string
}

func (r *CognitionTurnRunner) assemblePrompt(ctx context.Context, req TurnRequest) (assembledPrompt, error) {
	if r == nil || r.promptAssemble == nil {
		return assembledPrompt{}, errors.New("prompt.assemble requires an attached Python worker")
	}
	input := map[string]any{
		"message":    req.Prompt,
		"session_id": req.SessionID,
		"plan_mode":  req.PlanMode,
		"chat_mode":  req.ChatMode,
	}
	if req.Model != nil && strings.TrimSpace(*req.Model) != "" {
		input["model"] = strings.TrimSpace(*req.Model)
	}
	if req.Provider != nil && strings.TrimSpace(*req.Provider) != "" {
		input["provider"] = strings.TrimSpace(*req.Provider)
	}
	raw, err := json.Marshal(input)
	if err != nil {
		return assembledPrompt{}, err
	}
	res, err := r.promptAssemble.Execute(ctx, tools.Request{
		ToolID:  "prompt.assemble",
		Version: 1,
		Input:   raw,
	})
	if err != nil {
		return assembledPrompt{}, err
	}
	var out struct {
		System string `json:"system"`
		Goal   string `json:"goal"`
	}
	if err := json.Unmarshal(res.Output, &out); err != nil {
		return assembledPrompt{}, fmt.Errorf("prompt.assemble decode: %w", err)
	}
	if strings.TrimSpace(out.System) == "" {
		return assembledPrompt{}, errors.New("prompt.assemble returned empty system")
	}
	goal := out.Goal
	if strings.TrimSpace(goal) == "" {
		goal = req.Prompt
	}
	return assembledPrompt{System: out.System, Goal: goal}, nil
}

func (r *CognitionTurnRunner) RunTurn(ctx context.Context, req TurnRequest, emit func(string) error) error {
	if r == nil {
		return errors.New("cognition turn runner is nil")
	}
	live := r.modelForTurn(req)
	if live == nil {
		return errors.New("cognition turn runner requires a model")
	}
	if r.Tools == nil {
		return errors.New("cognition turn runner requires a tool executor")
	}
	if r.Policy == nil {
		return errors.New("cognition turn runner requires a policy")
	}

	seed := cognition.Turn{Goal: req.Prompt}
	// Production serve always AttachPythonWorker. When attached, assemble is
	// mandatory — never fall back to raw prompt-only.
	if r.promptAssemble != nil {
		assembled, err := r.assemblePrompt(ctx, req)
		if err != nil {
			return fmt.Errorf("prompt.assemble required: %w", err)
		}
		seed.System = assembled.System
		seed.Goal = assembled.Goal
	}

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

	out := r.runEngine(ctx, req, live, seed, safeEmit)

	// Credentialed-but-unusable (401/402/403, subscription) — switch once to
	// another provider so selecting Poe without a sub doesn't hard-fail chat.
	if out.Err != nil && isProviderUnusableError(out.Err) && strings.TrimSpace(r.HomeDir) != "" {
		exclude := ""
		if req.Provider != nil {
			exclude = strings.TrimSpace(*req.Provider)
		}
		if exclude == "" {
			cfgMap := LoadConfig(r.HomeDir)
			exclude = cfgString(cfgMap, "llm_provider", "")
		}
		alt := resolveChatModel(r.HomeDir, "", "", "", exclude)
		if alt != nil && !sameChatEndpoint(live, alt) {
			if oc, ok := alt.(*providers.OpenAICompat); ok {
				r.applyToolSchemas(oc)
			}
			safeEmit("@@status:That provider isn't available right now — switching to your usual model…\n")
			out = r.runEngine(ctx, req, alt, seed, safeEmit)
		}
	}

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

func (r *CognitionTurnRunner) runEngine(
	ctx context.Context,
	req TurnRequest,
	live cognition.Model,
	seed cognition.Turn,
	safeEmit func(string),
) cognition.Outcome {
	execTools := r.Tools
	policy := r.Policy
	model := cognition.Model(&emittingModel{inner: live, emit: safeEmit})
	if req.DrainNudges != nil {
		model = &nudgeAwareModel{inner: model, drain: req.DrainNudges, emit: safeEmit}
	}
	resolveTool := func(name string) string {
		if oc, ok := live.(*providers.OpenAICompat); ok {
			return oc.ResolveToolName(name)
		}
		return name
	}
	execTools = &abiNameTools{inner: execTools, resolve: resolveTool}
	policy = &abiNamePolicy{inner: policy, resolve: resolveTool}
	cfg := r.Config
	if req.MaxIterations > 0 {
		cfg.MaxIterations = req.MaxIterations
	}
	engine := cognition.Engine{
		Model:  model,
		Tools:  &emittingTools{inner: execTools, emit: safeEmit},
		Policy: policy,
		Config: cfg,
	}
	return engine.RunTurn(ctx, seed)
}

func sameChatEndpoint(a, b cognition.Model) bool {
	if a == nil || b == nil {
		return a == b
	}
	oa, oka := a.(*providers.OpenAICompat)
	ob, okb := b.(*providers.OpenAICompat)
	if oka && okb {
		return strings.EqualFold(strings.TrimSpace(oa.BaseURL), strings.TrimSpace(ob.BaseURL)) &&
			strings.EqualFold(strings.TrimSpace(oa.Model), strings.TrimSpace(ob.Model))
	}
	return a == b
}

type nudgeAwareModel struct {
	inner cognition.Model
	drain func() []string
	emit  func(string)
}

func (m *nudgeAwareModel) Stream(ctx context.Context, turn cognition.Turn) (<-chan cognition.ModelEvent, error) {
	if m.drain != nil {
		if nudges := m.drain(); len(nudges) > 0 {
			if m.emit != nil {
				m.emit("@@steered\n")
			}
			turn.Goal = strings.TrimSpace(turn.Goal) +
				"\n\n[Owner mid-turn guidance]\n" + strings.Join(nudges, "\n")
		}
	}
	return m.inner.Stream(ctx, turn)
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

// abiNameTools remaps sanitized OpenAI function names → Tool ABI ids at execute.
type abiNameTools struct {
	inner   cognition.ToolExecutor
	resolve func(string) string
}

func (t *abiNameTools) Execute(ctx context.Context, call cognition.ToolCall) cognition.ToolResult {
	if t != nil && t.resolve != nil {
		call.Name = t.resolve(call.Name)
	}
	return t.inner.Execute(ctx, call)
}

type abiNamePolicy struct {
	inner   cognition.Policy
	resolve func(string) string
}

func (p *abiNamePolicy) Decide(ctx context.Context, call cognition.ToolCall) cognition.Decision {
	if p != nil && p.resolve != nil {
		call.Name = p.resolve(call.Name)
	}
	return p.inner.Decide(ctx, call)
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
