package tools

import (
	"context"
	"encoding/json"
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"testing"

	"github.com/AhmiDarrow/RemedyAI/native/go/core"
)

func TestRegisterZigHostToolsDescriptors(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	for _, id := range []string{"computer.screenshot", "computer.windows", "computer.monitors", "computer.snapshot"} {
		desc, err := registry.Latest(id)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		if desc.Runtime != RuntimeZig {
			t.Fatalf("%s runtime=%s", id, desc.Runtime)
		}
		if desc.Risk != RiskReadOnly {
			t.Fatalf("%s risk=%v", id, desc.Risk)
		}
		if len(desc.Capabilities) == 0 || desc.Capabilities[0] != "computer.read" {
			t.Fatalf("%s capabilities=%v", id, desc.Capabilities)
		}
	}
	for _, id := range []string{"computer.click", "computer.type"} {
		desc, err := registry.Latest(id)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		if desc.Runtime != RuntimeZig || desc.Risk != RiskMutation {
			t.Fatalf("%s runtime/risk=%s/%v", id, desc.Runtime, desc.Risk)
		}
		if len(desc.Capabilities) == 0 || desc.Capabilities[0] != "computer.input" {
			t.Fatalf("%s capabilities=%v", id, desc.Capabilities)
		}
	}
	shell, err := registry.Latest("shell.exec")
	if err != nil {
		t.Fatal(err)
	}
	if shell.Runtime != RuntimeZig || shell.Risk != RiskMutation {
		t.Fatalf("shell.exec runtime/risk=%s/%v", shell.Runtime, shell.Risk)
	}
	if len(shell.Capabilities) == 0 || shell.Capabilities[0] != "process.spawn" {
		t.Fatalf("shell.exec capabilities=%v", shell.Capabilities)
	}
}

func TestZigHostToolsFailClosedWithoutLibrary(t *testing.T) {
	core.ResetForTest()
	t.Cleanup(core.ResetForTest)
	t.Setenv("REMEDY_NATIVE_CORE_LIB", filepath.Join(t.TempDir(), "missing.dll"))

	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	token := []byte("test-token")
	_, err := registry.Execute(context.Background(), Request{
		ToolID: "computer.windows", Version: 1, Input: json.RawMessage(`{}`),
		CapabilityToken: token,
	})
	if err == nil {
		t.Fatal("expected fail-closed error")
	}
	if !errors.Is(err, core.ErrUnavailable) && !strings.Contains(err.Error(), "unavailable") {
		t.Fatalf("err=%v", err)
	}

	_, err = registry.Execute(context.Background(), Request{
		ToolID: "computer.snapshot", Version: 1, Input: json.RawMessage(`{}`),
		CapabilityToken: token,
	})
	if err == nil {
		t.Fatal("computer.snapshot expected fail-closed error")
	}
	if !errors.Is(err, core.ErrUnavailable) && !strings.Contains(err.Error(), "unavailable") {
		t.Fatalf("computer.snapshot err=%v", err)
	}

	_, err = registry.Execute(context.Background(), Request{
		ToolID: "computer.click", Version: 1, Input: json.RawMessage(`{"x":10,"y":20}`),
		CapabilityToken: token,
	})
	if err == nil {
		t.Fatal("computer.click expected fail-closed error")
	}
	if !errors.Is(err, core.ErrUnavailable) && !strings.Contains(err.Error(), "unavailable") {
		t.Fatalf("computer.click err=%v", err)
	}

	_, err = registry.Execute(context.Background(), Request{
		ToolID: "computer.type", Version: 1, Input: json.RawMessage(`{"text":"hi"}`),
		CapabilityToken: token,
	})
	if err == nil {
		t.Fatal("computer.type expected fail-closed error")
	}
	if !errors.Is(err, core.ErrUnavailable) && !strings.Contains(err.Error(), "unavailable") {
		t.Fatalf("computer.type err=%v", err)
	}
}

func TestComputerClickRejectsBadButton(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.click",
		Version:         1,
		Input:           json.RawMessage(`{"x":1,"y":2,"button":"side"}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("bad button: %v", err)
	}
}

func TestComputerTypeRejectsEmptyText(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.type",
		Version:         1,
		Input:           json.RawMessage(`{"text":""}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("empty text: %v", err)
	}
}

func TestShellExecRejectsRelativeArgv(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "shell.exec",
		Version:         1,
		Input:           json.RawMessage(`{"argv":["echo","hi"]}`),
		CapabilityToken: []byte("test-token"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("relative argv[0]: %v", err)
	}
	if !strings.Contains(err.Error(), "absolute") {
		t.Fatalf("expected absolute path message: %v", err)
	}
}

func TestShellExecRequiresCapabilityToken(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:  "shell.exec",
		Version: 1,
		Input:   json.RawMessage(`{"argv":["C:\\\\Windows\\\\System32\\\\cmd.exe","/c","echo","hi"]}`),
	})
	if !errors.Is(err, ErrAuthorizationRequired) {
		t.Fatalf("err=%v", err)
	}
}

func TestShellExecFailClosedWithoutLibrary(t *testing.T) {
	core.ResetForTest()
	t.Cleanup(core.ResetForTest)
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)
	t.Setenv("REMEDY_NATIVE_CORE_LIB", filepath.Join(t.TempDir(), "missing.dll"))

	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	argv0 := filepath.Join(home, "bin", "tool.exe")
	input, err := json.Marshal(map[string]any{"argv": []string{argv0, "--version"}})
	if err != nil {
		t.Fatal(err)
	}
	_, err = registry.Execute(context.Background(), Request{
		ToolID: "shell.exec", Version: 1, Input: input, CapabilityToken: []byte("tok"),
	})
	if err == nil {
		t.Fatal("expected fail-closed error")
	}
	if !errors.Is(err, core.ErrUnavailable) && !strings.Contains(err.Error(), "unavailable") {
		t.Fatalf("err=%v", err)
	}
}

func TestZigHostToolsLiveWhenLibraryPresent(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows host surface")
	}
	core.ResetForTest()
	t.Cleanup(core.ResetForTest)
	if core.FindLibraryPath() == "" {
		t.Skip("remedy_core.dll not built")
	}
	home := t.TempDir()
	t.Setenv("REMEDY_HOME", home)

	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	token := []byte("test-token")

	monitors, err := registry.Execute(context.Background(), Request{
		ToolID: "computer.monitors", Version: 1, Input: json.RawMessage(`{}`),
		CapabilityToken: token,
	})
	if err != nil {
		t.Fatalf("computer.monitors: %v", err)
	}
	var monOut struct {
		Monitors []any `json:"monitors"`
		Total    int   `json:"total"`
	}
	if err := json.Unmarshal(monitors.Output, &monOut); err != nil {
		t.Fatal(err)
	}
	if monOut.Total < 1 || len(monOut.Monitors) < 1 {
		t.Fatalf("expected at least one monitor: %#v", monOut)
	}

	windows, err := registry.Execute(context.Background(), Request{
		ToolID: "computer.windows", Version: 1, Input: json.RawMessage(`{"limit":10}`),
		CapabilityToken: token,
	})
	if err != nil {
		t.Fatalf("computer.windows: %v", err)
	}
	var winOut struct {
		Windows []any `json:"windows"`
		Total   int   `json:"total"`
	}
	if err := json.Unmarshal(windows.Output, &winOut); err != nil {
		t.Fatal(err)
	}
	if winOut.Total != len(winOut.Windows) {
		t.Fatalf("total mismatch: %#v", winOut)
	}

	shot, err := registry.Execute(context.Background(), Request{
		ToolID: "computer.screenshot", Version: 1,
		Input:           json.RawMessage(`{"label":"tool-abi"}`),
		CapabilityToken: token,
	})
	if err != nil {
		t.Fatalf("computer.screenshot: %v", err)
	}
	var shotOut struct {
		Path   string `json:"path"`
		Width  int    `json:"width"`
		Height int    `json:"height"`
	}
	if err := json.Unmarshal(shot.Output, &shotOut); err != nil {
		t.Fatal(err)
	}
	if shotOut.Width < 1 || shotOut.Height < 1 {
		t.Fatalf("bad shot geometry: %#v", shotOut)
	}
	if _, err := os.Stat(shotOut.Path); err != nil {
		t.Fatalf("shot missing: %v", err)
	}
	if !strings.Contains(filepath.ToSlash(shotOut.Path), "/computer/shots/") {
		t.Fatalf("unexpected shot path %q", shotOut.Path)
	}

	snap, err := registry.Execute(context.Background(), Request{
		ToolID: "computer.snapshot", Version: 1,
		Input:           json.RawMessage(`{"max_elements":20}`),
		CapabilityToken: token,
	})
	if err != nil {
		t.Fatalf("computer.snapshot: %v", err)
	}
	var snapOut struct {
		Source    string `json:"source"`
		Available bool   `json:"available"`
		Controls  []any  `json:"controls"`
		Total     int    `json:"total"`
	}
	if err := json.Unmarshal(snap.Output, &snapOut); err != nil {
		t.Fatal(err)
	}
	if snapOut.Source != "uia" {
		t.Fatalf("source=%q", snapOut.Source)
	}
	if snapOut.Total != len(snapOut.Controls) {
		t.Fatalf("total mismatch: %#v", snapOut)
	}

	cmd := filepath.Join(os.Getenv("SystemRoot"), "System32", "cmd.exe")
	if _, err := os.Stat(cmd); err != nil {
		t.Skip("cmd.exe missing")
	}
	shellInput, err := json.Marshal(map[string]any{
		"argv":       []string{cmd, "/d", "/s", "/c", "echo shell-exec-ok"},
		"timeout_ms": 15000,
	})
	if err != nil {
		t.Fatal(err)
	}
	shell, err := registry.Execute(context.Background(), Request{
		ToolID: "shell.exec", Version: 1, Input: shellInput, CapabilityToken: token,
	})
	if err != nil {
		t.Fatalf("shell.exec: %v", err)
	}
	var shellOut struct {
		ExitCode uint32 `json:"exit_code"`
		TimedOut bool   `json:"timed_out"`
		Stdout   string `json:"stdout"`
		Stderr   string `json:"stderr"`
	}
	if err := json.Unmarshal(shell.Output, &shellOut); err != nil {
		t.Fatal(err)
	}
	if shellOut.TimedOut || shellOut.ExitCode != 0 {
		t.Fatalf("shell.exec failed: %#v", shellOut)
	}
	if !strings.Contains(shellOut.Stdout, "shell-exec-ok") {
		t.Fatalf("stdout=%q stderr=%q", shellOut.Stdout, shellOut.Stderr)
	}
}
