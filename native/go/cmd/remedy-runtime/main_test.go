package main

import (
	"runtime"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/httpapi"
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

func TestServeWiringUsesNativeVoiceAndVision(t *testing.T) {
	// Mirrors main.go: both are native and need no worker to exist.
	voice := httpapi.NewNativeVoiceWorker()
	defer voice.Close()
	cfg := httpapi.Config{VoiceWorker: voice, VisionWorker: httpapi.NewNativeVisionWorker()}
	if cfg.VoiceWorker == nil || cfg.VisionWorker == nil {
		t.Fatal("serve Config must carry voice and vision")
	}
}
