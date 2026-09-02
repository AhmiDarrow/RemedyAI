// Package tools defines the language-neutral Remedy Tool ABI and registry.
package tools

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"sync"

	"github.com/santhosh-tekuri/jsonschema/v6"
)

var (
	ErrInvalidDescriptor = errors.New("invalid tool descriptor")
	ErrAlreadyRegistered = errors.New("tool version is already registered")
	ErrToolNotFound      = errors.New("tool version is not registered")
	ErrInvalidInput      = errors.New("tool input failed schema validation")
	ErrInvalidOutput     = errors.New("tool output failed schema validation")
)

type Risk uint8

const (
	RiskReadOnly Risk = iota
	RiskMutation
	RiskCheckpoint
)

type Runtime string

const (
	RuntimeGo     Runtime = "go"
	RuntimeZig    Runtime = "zig"
	RuntimePython Runtime = "python"
	RuntimeWASM   Runtime = "wasm"
)

type Descriptor struct {
	ID           string          `json:"id"`
	Version      uint32          `json:"version"`
	Description  string          `json:"description"`
	Runtime      Runtime         `json:"runtime"`
	Risk         Risk            `json:"risk"`
	Capabilities []string        `json:"capabilities"`
	Permissions  []string        `json:"permissions"`
	InputSchema  json.RawMessage `json:"input_schema"`
	OutputSchema json.RawMessage `json:"output_schema"`
}

type Request struct {
	ToolID          string
	Version         uint32
	Input           json.RawMessage
	CapabilityToken []byte
}
type Result struct {
	Output   json.RawMessage
	Evidence []byte
}
type Executor interface {
	Execute(context.Context, Request) (Result, error)
}
type ExecutorFunc func(context.Context, Request) (Result, error)

func (f ExecutorFunc) Execute(ctx context.Context, request Request) (Result, error) {
	return f(ctx, request)
}

type registered struct {
	descriptor    Descriptor
	input, output *jsonschema.Schema
	executor      Executor
}
type key struct {
	id      string
	version uint32
}
type Registry struct {
	mu    sync.RWMutex
	tools map[key]registered
}

func NewRegistry() *Registry { return &Registry{tools: make(map[key]registered)} }

func (r *Registry) Register(descriptor Descriptor, executor Executor) error {
	if descriptor.ID == "" || descriptor.Version == 0 || descriptor.Description == "" || executor == nil {
		return ErrInvalidDescriptor
	}
	if descriptor.Runtime != RuntimeGo && descriptor.Runtime != RuntimeZig && descriptor.Runtime != RuntimePython && descriptor.Runtime != RuntimeWASM {
		return ErrInvalidDescriptor
	}
	input, err := compileSchema(descriptor.ID+"-input", descriptor.InputSchema)
	if err != nil {
		return fmt.Errorf("%w: input: %v", ErrInvalidDescriptor, err)
	}
	output, err := compileSchema(descriptor.ID+"-output", descriptor.OutputSchema)
	if err != nil {
		return fmt.Errorf("%w: output: %v", ErrInvalidDescriptor, err)
	}
	k := key{descriptor.ID, descriptor.Version}
	r.mu.Lock()
	defer r.mu.Unlock()
	if _, exists := r.tools[k]; exists {
		return ErrAlreadyRegistered
	}
	descriptor.Capabilities = append([]string(nil), descriptor.Capabilities...)
	descriptor.Permissions = append([]string(nil), descriptor.Permissions...)
	descriptor.InputSchema = append(json.RawMessage(nil), descriptor.InputSchema...)
	descriptor.OutputSchema = append(json.RawMessage(nil), descriptor.OutputSchema...)
	r.tools[k] = registered{descriptor: descriptor, input: input, output: output, executor: executor}
	return nil
}

func (r *Registry) Resolve(id string, version uint32) (Descriptor, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	tool, ok := r.tools[key{id, version}]
	if !ok {
		return Descriptor{}, ErrToolNotFound
	}
	return tool.descriptor, nil
}

func (r *Registry) Latest(id string) (Descriptor, error) {
	r.mu.RLock()
	defer r.mu.RUnlock()
	var latest Descriptor
	for k, tool := range r.tools {
		if k.id == id && k.version > latest.Version {
			latest = tool.descriptor
		}
	}
	if latest.Version == 0 {
		return Descriptor{}, ErrToolNotFound
	}
	return latest, nil
}

func (r *Registry) Execute(ctx context.Context, request Request) (Result, error) {
	r.mu.RLock()
	tool, ok := r.tools[key{request.ToolID, request.Version}]
	r.mu.RUnlock()
	if !ok {
		return Result{}, ErrToolNotFound
	}
	input, err := decodeJSON(request.Input)
	if err != nil || tool.input.Validate(input) != nil {
		return Result{}, ErrInvalidInput
	}
	result, err := tool.executor.Execute(ctx, request)
	if err != nil {
		return result, err
	}
	output, err := decodeJSON(result.Output)
	if err != nil || tool.output.Validate(output) != nil {
		return Result{}, ErrInvalidOutput
	}
	return result, nil
}

func compileSchema(name string, raw json.RawMessage) (*jsonschema.Schema, error) {
	document, err := jsonschema.UnmarshalJSON(bytes.NewReader(raw))
	if err != nil {
		return nil, err
	}
	compiler := jsonschema.NewCompiler()
	compiler.AssertFormat()
	compiler.UseLoader(denyLoader{})
	location := "urn:remedy:tool:" + name
	if err := compiler.AddResource(location, document); err != nil {
		return nil, err
	}
	return compiler.Compile(location)
}

type denyLoader struct{}

func (denyLoader) Load(string) (any, error) {
	return nil, errors.New("external schema references are disabled")
}

func decodeJSON(raw []byte) (any, error) {
	decoder := json.NewDecoder(bytes.NewReader(raw))
	decoder.UseNumber()
	var value any
	if err := decoder.Decode(&value); err != nil {
		return nil, err
	}
	var trailing any
	if err := decoder.Decode(&trailing); !errors.Is(err, io.EOF) {
		return nil, errors.New("multiple JSON values")
	}
	return value, nil
}
