package tools

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"runtime"
)

const (
	protocolVersion = 1
	toolABIVersion  = 1
)

// RegisterGoBuiltins installs in-process Go Tool ABI executors.
// Host capture / window / monitor tools live in RegisterZigHostTools;
// these builtins are Go-owned runtime and pure-data helpers only.
func RegisterGoBuiltins(registry *Registry) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}
	if err := registry.Register(Descriptor{
		ID:          "runtime.probe",
		Version:     1,
		Description: "Emit the native runtime probe record",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{"type":"object","additionalProperties":false}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["status","protocol","tool_abi","os","arch"],
			"properties":{
				"status":{"type":"string","const":"ready"},
				"protocol":{"type":"integer","const":1},
				"tool_abi":{"type":"integer","const":1},
				"os":{"type":"string","minLength":1},
				"arch":{"type":"string","minLength":1}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(context.Context, Request) (Result, error) {
		out, err := json.Marshal(map[string]any{
			"status":   "ready",
			"protocol": protocolVersion,
			"tool_abi": toolABIVersion,
			"os":       runtime.GOOS,
			"arch":     runtime.GOARCH,
		})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "json.canonical",
		Version:     1,
		Description: "Re-encode JSON with Go map key ordering",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["value"],
			"properties":{"value":{}},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["canonical"],
			"properties":{"canonical":{"type":"string","minLength":1}},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			Value json.RawMessage `json:"value"`
		}
		if err := json.Unmarshal(request.Input, &body); err != nil || len(body.Value) == 0 {
			return Result{}, ErrInvalidInput
		}
		var value any
		if err := json.Unmarshal(body.Value, &value); err != nil {
			return Result{}, ErrInvalidInput
		}
		canonical, err := json.Marshal(value)
		if err != nil {
			return Result{}, err
		}
		out, err := json.Marshal(map[string]string{"canonical": string(canonical)})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:          "text.sha256",
		Version:     1,
		Description: "SHA-256 hex digest of a UTF-8 string",
		Runtime:     RuntimeGo,
		Risk:        RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["text"],
			"properties":{"text":{"type":"string"}},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["sha256"],
			"properties":{"sha256":{"type":"string","minLength":64,"maxLength":64}},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(func(_ context.Context, request Request) (Result, error) {
		var body struct {
			Text string `json:"text"`
		}
		if err := json.Unmarshal(request.Input, &body); err != nil {
			return Result{}, ErrInvalidInput
		}
		sum := sha256.Sum256([]byte(body.Text))
		out, err := json.Marshal(map[string]string{"sha256": hex.EncodeToString(sum[:])})
		return Result{Output: out}, err
	})); err != nil {
		return err
	}

	return nil
}
