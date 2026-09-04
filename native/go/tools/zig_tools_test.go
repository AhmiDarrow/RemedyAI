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
	for _, id := range []string{"computer.screenshot", "computer.windows", "computer.monitors"} {
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
}
