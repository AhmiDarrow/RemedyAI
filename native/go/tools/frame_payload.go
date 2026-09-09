package tools

import (
	"encoding/json"
	"errors"
	"fmt"
)

// Wire payloads for KindToolRequest / KindToolResult on RMDY frames.
// Shared by the Go registry executor and the Python tool worker.

// GoBoundField marks a request whose path-binding inputs (home_dir,
// workspace_root, project_path) were set by the Go runtime rather than the
// model. The Python worker ignores those keys unless the flag is present —
// either here on the envelope or inside the input object (which is where
// httpapi's injection helper sets it). Go must delete any model-supplied
// copy before setting it.
const GoBoundField = "_go_bound"

type WireRequest struct {
	ToolID          string          `json:"tool_id"`
	Version         uint32          `json:"version"`
	Input           json.RawMessage `json:"input"`
	CapabilityToken []byte          `json:"capability_token,omitempty"`
	// GoBound vouches for the path-binding keys in Input (see GoBoundField).
	GoBound bool `json:"_go_bound,omitempty"`
}

type WireResult struct {
	OK       bool            `json:"ok"`
	Output   json.RawMessage `json:"output,omitempty"`
	Evidence []byte          `json:"evidence,omitempty"`
	Error    string          `json:"error,omitempty"`
}

func MarshalWireRequest(request Request) ([]byte, error) {
	if request.ToolID == "" || request.Version == 0 {
		return nil, errors.New("tool request requires id and version")
	}
	input := request.Input
	if len(input) == 0 {
		input = json.RawMessage(`{}`)
	}
	return json.Marshal(WireRequest{
		ToolID:          request.ToolID,
		Version:         request.Version,
		Input:           input,
		CapabilityToken: append([]byte(nil), request.CapabilityToken...),
	})
}

func UnmarshalWireRequest(payload []byte) (Request, error) {
	var wire WireRequest
	if err := json.Unmarshal(payload, &wire); err != nil {
		return Request{}, err
	}
	if wire.ToolID == "" || wire.Version == 0 {
		return Request{}, errors.New("tool request requires id and version")
	}
	input := wire.Input
	if len(input) == 0 {
		input = json.RawMessage(`{}`)
	}
	return Request{
		ToolID:          wire.ToolID,
		Version:         wire.Version,
		Input:           append(json.RawMessage(nil), input...),
		CapabilityToken: append([]byte(nil), wire.CapabilityToken...),
	}, nil
}

func MarshalWireResult(result Result, callErr error) ([]byte, error) {
	if callErr != nil {
		return json.Marshal(WireResult{OK: false, Error: callErr.Error()})
	}
	return json.Marshal(WireResult{
		OK:       true,
		Output:   append(json.RawMessage(nil), result.Output...),
		Evidence: append([]byte(nil), result.Evidence...),
	})
}

func UnmarshalWireResult(payload []byte) (Result, error) {
	var wire WireResult
	if err := json.Unmarshal(payload, &wire); err != nil {
		return Result{}, err
	}
	if !wire.OK {
		if wire.Error == "" {
			return Result{}, errors.New("tool worker returned failure without error")
		}
		return Result{}, fmt.Errorf("%s", wire.Error)
	}
	if len(wire.Output) == 0 {
		return Result{}, errors.New("tool worker returned empty output")
	}
	return Result{
		Output:   append(json.RawMessage(nil), wire.Output...),
		Evidence: append([]byte(nil), wire.Evidence...),
	}, nil
}
