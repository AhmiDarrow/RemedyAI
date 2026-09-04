package tools

import (
	"context"
	"crypto/rand"
	"errors"
	"fmt"

	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

// FrameCaller sends one RMDY request and waits for the correlated response.
// ipc.Client satisfies this contract.
type FrameCaller interface {
	Call(context.Context, protocol.Frame) (protocol.Frame, error)
}

// RMDYExecutor runs Tool ABI requests over KindToolRequest / KindToolResult frames.
type RMDYExecutor struct {
	Caller FrameCaller
}

func NewRMDYExecutor(caller FrameCaller) *RMDYExecutor {
	return &RMDYExecutor{Caller: caller}
}

func (e *RMDYExecutor) Execute(ctx context.Context, request Request) (Result, error) {
	if e == nil || e.Caller == nil {
		return Result{}, errors.New("rmdy tool executor requires a frame caller")
	}
	payload, err := MarshalWireRequest(request)
	if err != nil {
		return Result{}, err
	}
	var correlationID [16]byte
	if _, err := rand.Read(correlationID[:]); err != nil {
		return Result{}, err
	}
	response, err := e.Caller.Call(ctx, protocol.Frame{
		Kind:          protocol.KindToolRequest,
		CorrelationID: correlationID,
		Payload:       payload,
	})
	if err != nil {
		return Result{}, err
	}
	if response.Kind != protocol.KindToolResult {
		return Result{}, fmt.Errorf("unexpected RMDY frame kind %d", response.Kind)
	}
	if response.Flags&1 != 0 {
		msg := string(response.Payload)
		if msg == "" {
			msg = "tool worker transport error"
		}
		return Result{}, errors.New(msg)
	}
	return UnmarshalWireResult(response.Payload)
}
