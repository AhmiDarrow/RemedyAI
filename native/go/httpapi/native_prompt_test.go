package httpapi

import (
	"context"
	"os"
	"path/filepath"
	"strings"
	"testing"
)

func TestTheRunnerBuildsItsPromptInGo(t *testing.T) {
	home := t.TempDir()
	proj := filepath.Join(t.TempDir(), "proj")
	if err := os.MkdirAll(proj, 0o755); err != nil {
		t.Fatal(err)
	}
	writeCfg := func(extra string) {
		cfg := extra + "project_path = '" + proj + "'\n"
		if err := os.WriteFile(filepath.Join(home, "config.toml"), []byte(cfg), 0o600); err != nil {
			t.Fatal(err)
		}
	}
	writeCfg("")
	req := TurnRequest{SessionID: "s1", Prompt: "hello", ProjectPath: proj}

	// A bare runner (no stores, no worker) has no prompt source at all.
	bare := &CognitionTurnRunner{HomeDir: home}
	if bare.nativeOn() {
		t.Fatal("a runner without stores must not claim a native prompt")
	}
	if _, err := bare.assemblePrompt(context.Background(), req); err == nil {
		t.Fatal("with neither stores nor a worker, assembling must fail, not send an empty prompt")
	}

	// With the server's stores it is native by default, and no worker is needed.
	r := &CognitionTurnRunner{HomeDir: home, PromptSources: serverPromptSources{}}
	if !r.nativeOn() {
		t.Fatal("native prompt is the default")
	}
	got, err := r.assemblePrompt(context.Background(), req)
	if err != nil {
		t.Fatal(err)
	}
	if strings.TrimSpace(got.System) == "" || strings.Contains(got.System, "[Turn context") {
		t.Fatalf("system prompt must be present and hold no per-turn context: %.200q", got.System)
	}
	if !strings.HasPrefix(got.Context, "[Turn context") || got.Goal != "hello" {
		t.Fatalf("context=%.80q goal=%q", got.Context, got.Goal)
	}

	// The owner can still send the prompt hooks to an attached worker.
	writeCfg("native_prompt = false\n")
	if r.nativeOn() {
		t.Fatal("native_prompt = false turns the native prompt off")
	}
}
