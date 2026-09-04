package httpapi

import (
	"context"
	"encoding/json"

	"github.com/AhmiDarrow/RemedyAI/native/go/cognition"
	"github.com/AhmiDarrow/RemedyAI/native/go/tools"
)

// runtimeLocalAuthorizer accepts any non-empty capability token for process-local
// turn execution. Presence of the token is the gate; the registry already
// refuses protected tools when the token is missing.
func runtimeLocalAuthorizer(_ context.Context, _ tools.Descriptor, _ tools.Request) error {
	return nil
}

// RuntimeCapabilityToken returns a process-local token for protected tools.
func RuntimeCapabilityToken(desc tools.Descriptor) []byte {
	protected := desc.Risk != tools.RiskReadOnly || len(desc.Capabilities) != 0 || len(desc.Permissions) != 0
	if !protected {
		return nil
	}
	return []byte("runtime-local")
}

// RegistryToolExecutor adapts the Tool ABI registry to cognition.ToolExecutor.
type RegistryToolExecutor struct {
	Registry *tools.Registry
	// TokenFor supplies capability tokens for protected tools. Nil is valid when
	// every registered tool is unrestricted read-only.
	TokenFor func(tools.Descriptor) []byte
}

func (e *RegistryToolExecutor) Execute(ctx context.Context, call cognition.ToolCall) cognition.ToolResult {
	if e == nil || e.Registry == nil {
		return cognition.ToolResult{ID: call.ID, Name: call.Name, Err: "tool registry is not configured"}
	}
	desc, err := e.Registry.Latest(call.Name)
	if err != nil {
		return cognition.ToolResult{ID: call.ID, Name: call.Name, Err: err.Error()}
	}
	input := json.RawMessage(call.Input)
	if len(input) == 0 {
		input = json.RawMessage(`{}`)
	}
	req := tools.Request{
		ToolID:  desc.ID,
		Version: desc.Version,
		Input:   append(json.RawMessage(nil), input...),
	}
	if e.TokenFor != nil {
		req.CapabilityToken = e.TokenFor(desc)
	}
	result, err := e.Registry.Execute(ctx, req)
	if err != nil {
		return cognition.ToolResult{ID: call.ID, Name: call.Name, Err: err.Error()}
	}
	return cognition.ToolResult{ID: call.ID, Name: call.Name, Output: append([]byte(nil), result.Output...)}
}

// RegistryPolicy allows registered read-only tools, asks for mutation/checkpoint,
// and denies unknown names. No silent allow for unregistered tools.
type RegistryPolicy struct {
	Registry *tools.Registry
}

func (p *RegistryPolicy) Decide(_ context.Context, call cognition.ToolCall) cognition.Decision {
	if p == nil || p.Registry == nil {
		return cognition.Deny
	}
	desc, err := p.Registry.Latest(call.Name)
	if err != nil {
		return cognition.Deny
	}
	switch desc.Risk {
	case tools.RiskReadOnly:
		return cognition.Allow
	case tools.RiskMutation, tools.RiskCheckpoint:
		return cognition.Ask
	default:
		return cognition.Deny
	}
}

// NewDefaultToolRegistry builds the turn-time Tool ABI registry.
// Always registers Go builtins and RuntimeZig host tools (execute fails closed
// when remedy_core is unavailable). pythonCaller, when non-nil, registers
// RuntimePython tools over RMDY frames.
func NewDefaultToolRegistry(pythonCaller tools.FrameCaller) (*tools.Registry, error) {
	registry := tools.NewRegistry(tools.AuthorizerFunc(runtimeLocalAuthorizer))
	if err := tools.RegisterGoBuiltins(registry); err != nil {
		return nil, err
	}
	if err := tools.RegisterZigHostTools(registry); err != nil {
		return nil, err
	}
	if pythonCaller != nil {
		if err := tools.RegisterPythonWorkerTools(registry, pythonCaller); err != nil {
			return nil, err
		}
	}
	return registry, nil
}
