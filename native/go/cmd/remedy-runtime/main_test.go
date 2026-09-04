package main

import (
	"context"
	"runtime"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
	"github.com/AhmiDarrow/RemedyAI/native/go/protocol"
)

func TestCurrentProbeDeclaresVersionedReadiness(t *testing.T) {
	got := currentProbe()
	if got.Status != "ready" || got.Protocol != 1 || got.ToolABI != 1 {
		t.Fatalf("unexpected probe: %+v", got)
	}
	if got.OS != runtime.GOOS || got.Arch != runtime.GOARCH {
		t.Fatalf("probe platform mismatch: %+v", got)
	}
}

func TestResolveServeAddrDefaultsToProduction7400(t *testing.T) {
	if got := resolveServeAddr("", true); got != defaultServeAddr {
		t.Fatalf("bare --serve = %q, want %q", got, defaultServeAddr)
	}
	if got := resolveServeAddr("127.0.0.1:7410", true); got != "127.0.0.1:7410" {
		t.Fatalf("--listen override = %q", got)
	}
	if got := resolveServeAddr("127.0.0.1:0", false); got != "127.0.0.1:0" {
		t.Fatalf("explicit --listen without --serve = %q", got)
	}
	if got := resolveServeAddr("", false); got != "" {
		t.Fatalf("neither flag = %q, want empty", got)
	}
}

type noopCaller struct{}

func (noopCaller) Call(context.Context, protocol.Frame) (protocol.Frame, error) {
	return protocol.Frame{Kind: protocol.KindToolResult, Payload: []byte(`{"ok":true,"output":{}}`)}, nil
}

func TestServeWiringAttachesNonNilVoiceAndVisionWorkers(t *testing.T) {
	// Mirrors main.go after StartRMDYToolWorker + AttachPythonWorker succeed:
	// AttachMLWorkers must yield non-nil cfg workers (fail closed on nil caller).
	if _, _, err := httpapi.AttachMLWorkers(nil); err == nil {
		t.Fatal("nil caller must fail closed")
	}
	voice, vision, err := httpapi.AttachMLWorkers(noopCaller{})
	if err != nil || voice == nil || vision == nil {
		t.Fatalf("attached workers voice=%v vision=%v err=%v", voice, vision, err)
	}
	cfg := httpapi.Config{VoiceWorker: voice, VisionWorker: vision}
	if cfg.VoiceWorker == nil || cfg.VisionWorker == nil {
		t.Fatal("serve Config must carry non-nil voice/vision workers")
	}
}
