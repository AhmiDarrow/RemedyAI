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
	for _, id := range []string{"computer.screenshot", "computer.print_window", "computer.windows", "computer.foreground", "computer.monitors", "computer.snapshot", "computer.uia.focused", "computer.uia.read_text"} {
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
	for _, id := range []string{"computer.click", "computer.type", "computer.key", "computer.key_hold", "computer.move", "computer.scroll", "computer.drag", "computer.focus", "computer.window", "computer.uia.action", "clipboard.write"} {
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
	for _, id := range []string{"clipboard.read", "clipboard.read_files", "clipboard.read_image"} {
		clipRead, err := registry.Latest(id)
		if err != nil {
			t.Fatalf("%s: %v", id, err)
		}
		if clipRead.Runtime != RuntimeZig || clipRead.Risk != RiskReadOnly {
			t.Fatalf("%s runtime/risk=%s/%v", id, clipRead.Runtime, clipRead.Risk)
		}
		if len(clipRead.Capabilities) == 0 || clipRead.Capabilities[0] != "computer.read" {
			t.Fatalf("%s capabilities=%v", id, clipRead.Capabilities)
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

	_, err = registry.Execute(context.Background(), Request{
		ToolID: "computer.key", Version: 1, Input: json.RawMessage(`{"key":"enter"}`),
		CapabilityToken: token,
	})
	if err == nil {
		t.Fatal("computer.key expected fail-closed error")
	}
	if !errors.Is(err, core.ErrUnavailable) && !strings.Contains(err.Error(), "unavailable") {
		t.Fatalf("computer.key err=%v", err)
	}

	_, err = registry.Execute(context.Background(), Request{
		ToolID: "computer.key_hold", Version: 1, Input: json.RawMessage(`{"key":"a","hold_ms":50}`),
		CapabilityToken: token,
	})
	if err == nil {
		t.Fatal("computer.key_hold expected fail-closed error")
	}
	if !errors.Is(err, core.ErrUnavailable) && !strings.Contains(err.Error(), "unavailable") {
		t.Fatalf("computer.key_hold err=%v", err)
	}

	for _, id := range []string{"computer.move", "computer.scroll", "clipboard.read", "clipboard.read_files", "clipboard.read_image", "clipboard.write", "computer.focus", "computer.window", "computer.print_window", "computer.foreground", "computer.uia.focused", "computer.uia.read_text", "computer.uia.action"} {
		input := json.RawMessage(`{"x":1,"y":2}`)
		switch id {
		case "computer.scroll":
			input = json.RawMessage(`{"x":1,"y":2,"dy":-1}`)
		case "clipboard.read", "clipboard.read_files", "clipboard.read_image", "computer.foreground", "computer.uia.focused":
			input = json.RawMessage(`{}`)
		case "clipboard.write":
			input = json.RawMessage(`{"text":"hi"}`)
		case "computer.focus", "computer.print_window":
			input = json.RawMessage(`{"hwnd":42}`)
		case "computer.window":
			input = json.RawMessage(`{"hwnd":42,"action":"minimize"}`)
		case "computer.uia.read_text":
			input = json.RawMessage(`{"hwnd":42}`)
		case "computer.uia.action":
			input = json.RawMessage(`{"hwnd":42,"name":"OK","action":"invoke"}`)
		}
		_, err = registry.Execute(context.Background(), Request{
			ToolID: id, Version: 1, Input: input, CapabilityToken: token,
		})
		if err == nil {
			t.Fatalf("%s expected fail-closed error", id)
		}
		if !errors.Is(err, core.ErrUnavailable) && !strings.Contains(err.Error(), "unavailable") {
			t.Fatalf("%s err=%v", id, err)
		}
	}

	_, err = registry.Execute(context.Background(), Request{
		ToolID: "computer.drag", Version: 1,
		Input:           json.RawMessage(`{"x1":1,"y1":2,"x2":3,"y2":4}`),
		CapabilityToken: token,
	})
	if err == nil {
		t.Fatal("computer.drag expected fail-closed error")
	}
	if !errors.Is(err, core.ErrUnavailable) && !strings.Contains(err.Error(), "unavailable") {
		t.Fatalf("computer.drag err=%v", err)
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

func TestComputerKeyRejectsUnknownKey(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.key",
		Version:         1,
		Input:           json.RawMessage(`{"key":"not-a-real-key"}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("unknown key: %v", err)
	}
}

func TestComputerKeyRejectsEmptyKey(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.key",
		Version:         1,
		Input:           json.RawMessage(`{"key":""}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("empty key: %v", err)
	}
}

func TestComputerKeyHoldRejectsCombo(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.key_hold",
		Version:         1,
		Input:           json.RawMessage(`{"key":"ctrl+s","hold_ms":100}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("combo key: %v", err)
	}
}

func TestComputerKeyHoldRejectsMissingHoldMS(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.key_hold",
		Version:         1,
		Input:           json.RawMessage(`{"key":"a"}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("missing hold_ms: %v", err)
	}
}

func TestComputerKeyHoldRejectsExcessiveHoldMS(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.key_hold",
		Version:         1,
		Input:           json.RawMessage(`{"key":"a","hold_ms":60001}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("excessive hold_ms: %v", err)
	}
}

func TestComputerKeyHoldRejectsUnknownKey(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.key_hold",
		Version:         1,
		Input:           json.RawMessage(`{"key":"not-a-real-key","hold_ms":10}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("unknown key: %v", err)
	}
}

func TestResolveKeyComboNamed(t *testing.T) {
	vks, err := resolveKeyCombo("ctrl+enter")
	if err != nil {
		t.Fatal(err)
	}
	if len(vks) != 2 || vks[0] != 0x11 || vks[1] != 0x0D {
		t.Fatalf("ctrl+enter => %v", vks)
	}
	vks, err = resolveKeyCombo("alt+f4")
	if err != nil {
		t.Fatal(err)
	}
	if len(vks) != 2 || vks[0] != 0x12 || vks[1] != 0x73 {
		t.Fatalf("alt+f4 => %v", vks)
	}
	vks, err = resolveKeyCombo("ArrowLeft")
	if err != nil {
		t.Fatal(err)
	}
	if len(vks) != 1 || vks[0] != 0x25 {
		t.Fatalf("ArrowLeft => %v", vks)
	}
	vks, err = resolveKeyCombo("ctrl+shift+s")
	if err != nil {
		t.Fatal(err)
	}
	if len(vks) != 3 || vks[0] != 0x11 || vks[1] != 0x10 || vks[2] != 0x53 {
		t.Fatalf("ctrl+shift+s => %v", vks)
	}
}

func TestComputerMoveRejectsMissingCoords(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.move",
		Version:         1,
		Input:           json.RawMessage(`{"x":1}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("missing y: %v", err)
	}
}

func TestComputerFocusRejectsMissingHwnd(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.focus",
		Version:         1,
		Input:           json.RawMessage(`{}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("missing hwnd: %v", err)
	}
	_, err = registry.Execute(context.Background(), Request{
		ToolID:          "computer.focus",
		Version:         1,
		Input:           json.RawMessage(`{"hwnd":0}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("zero hwnd: %v", err)
	}
}

func TestComputerPrintWindowRejectsMissingHwnd(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.print_window",
		Version:         1,
		Input:           json.RawMessage(`{}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("missing hwnd: %v", err)
	}
	_, err = registry.Execute(context.Background(), Request{
		ToolID:          "computer.print_window",
		Version:         1,
		Input:           json.RawMessage(`{"hwnd":0}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("zero hwnd: %v", err)
	}
}

func TestComputerWindowRejectsBadAction(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.window",
		Version:         1,
		Input:           json.RawMessage(`{"hwnd":42,"action":"explode"}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("bad action: %v", err)
	}
}

func TestComputerUIAReadTextRejectsMissingHwnd(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.uia.read_text",
		Version:         1,
		Input:           json.RawMessage(`{}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("missing hwnd: %v", err)
	}
	_, err = registry.Execute(context.Background(), Request{
		ToolID:          "computer.uia.read_text",
		Version:         1,
		Input:           json.RawMessage(`{"hwnd":0}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("zero hwnd: %v", err)
	}
}

func TestComputerUIAActionRejectsBadAction(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.uia.action",
		Version:         1,
		Input:           json.RawMessage(`{"hwnd":42,"name":"OK","action":"click"}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("bad action: %v", err)
	}
	_, err = registry.Execute(context.Background(), Request{
		ToolID:          "computer.uia.action",
		Version:         1,
		Input:           json.RawMessage(`{"hwnd":42,"name":"","action":"invoke"}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("empty name: %v", err)
	}
}

func TestComputerWindowMoveRequiresCoords(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.window",
		Version:         1,
		Input:           json.RawMessage(`{"hwnd":42,"action":"move"}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("move without x/y: %v", err)
	}
}

func TestComputerDragRejectsExcessiveSteps(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "computer.drag",
		Version:         1,
		Input:           json.RawMessage(`{"x1":0,"y1":0,"x2":1,"y2":1,"steps":999}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("excessive steps: %v", err)
	}
}

func TestClipboardWriteRejectsMissingText(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "clipboard.write",
		Version:         1,
		Input:           json.RawMessage(`{}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("missing text: %v", err)
	}
}

func TestClipboardReadFilesRejectsExtraFields(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "clipboard.read_files",
		Version:         1,
		Input:           json.RawMessage(`{"extra":true}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("extra fields: %v", err)
	}
}

func TestClipboardReadImageRejectsExtraFields(t *testing.T) {
	registry := NewRegistry(AuthorizerFunc(func(context.Context, Descriptor, Request) error {
		return nil
	}))
	if err := RegisterZigHostTools(registry); err != nil {
		t.Fatal(err)
	}
	_, err := registry.Execute(context.Background(), Request{
		ToolID:          "clipboard.read_image",
		Version:         1,
		Input:           json.RawMessage(`{"label":"x","extra":1}`),
		CapabilityToken: []byte("tok"),
	})
	if !errors.Is(err, ErrInvalidInput) {
		t.Fatalf("extra fields: %v", err)
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

	fg, err := registry.Execute(context.Background(), Request{
		ToolID: "computer.foreground", Version: 1, Input: json.RawMessage(`{}`),
		CapabilityToken: token,
	})
	if err != nil {
		t.Fatalf("computer.foreground: %v", err)
	}
	var fgOut struct {
		HWND  uint64 `json:"hwnd"`
		Title string `json:"title"`
		PID   uint32 `json:"pid"`
		Exe   string `json:"exe"`
	}
	if err := json.Unmarshal(fg.Output, &fgOut); err != nil {
		t.Fatal(err)
	}
	if fgOut.HWND != 0 && fgOut.PID == 0 {
		t.Fatalf("foreground hwnd set but pid empty: %#v", fgOut)
	}

	if winOut.Total > 0 {
		var first map[string]any
		rawWin, _ := json.Marshal(winOut.Windows[0])
		if err := json.Unmarshal(rawWin, &first); err != nil {
			t.Fatalf("decode window: %v", err)
		}
		var pwHwnd uint64
		switch v := first["hwnd"].(type) {
		case float64:
			pwHwnd = uint64(v)
		case json.Number:
			n, _ := v.Int64()
			pwHwnd = uint64(n)
		}
		if pwHwnd != 0 {
			pwInput, err := json.Marshal(map[string]any{"hwnd": pwHwnd, "label": "print-window"})
			if err != nil {
				t.Fatal(err)
			}
			pw, err := registry.Execute(context.Background(), Request{
				ToolID: "computer.print_window", Version: 1,
				Input: pwInput, CapabilityToken: token,
			})
			if err != nil {
				// Some HWNDs refuse PrintWindow; still require a typed failure, not panic.
				t.Logf("computer.print_window hwnd=%d: %v", pwHwnd, err)
			} else {
				var pwOut struct {
					Path   string `json:"path"`
					Width  int    `json:"width"`
					Height int    `json:"height"`
					HWND   uint64 `json:"hwnd"`
					Method string `json:"method"`
				}
				if err := json.Unmarshal(pw.Output, &pwOut); err != nil {
					t.Fatal(err)
				}
				if pwOut.Width < 1 || pwOut.Height < 1 || pwOut.HWND != pwHwnd || pwOut.Method != "PrintWindow" {
					t.Fatalf("bad print_window: %#v", pwOut)
				}
				if _, err := os.Stat(pwOut.Path); err != nil {
					t.Fatalf("print_window shot missing: %v", err)
				}
			}
		}
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

	focused, err := registry.Execute(context.Background(), Request{
		ToolID: "computer.uia.focused", Version: 1, Input: json.RawMessage(`{}`),
		CapabilityToken: token,
	})
	if err != nil {
		t.Fatalf("computer.uia.focused: %v", err)
	}
	var focusedOut struct {
		Available bool `json:"available"`
		Element   any  `json:"element"`
	}
	if err := json.Unmarshal(focused.Output, &focusedOut); err != nil {
		t.Fatal(err)
	}
	if !focusedOut.Available {
		t.Fatalf("expected UIA available for focused: %#v", focusedOut)
	}

	var fgHwnd uint64
	if winOut.Total > 0 {
		var first map[string]any
		rawWin, _ := json.Marshal(winOut.Windows[0])
		if err := json.Unmarshal(rawWin, &first); err == nil {
			switch v := first["hwnd"].(type) {
			case float64:
				fgHwnd = uint64(v)
			}
		}
	}
	if fgHwnd != 0 {
		readInput, err := json.Marshal(map[string]any{"hwnd": fgHwnd, "max_chars": 2000})
		if err != nil {
			t.Fatal(err)
		}
		readText, err := registry.Execute(context.Background(), Request{
			ToolID: "computer.uia.read_text", Version: 1, Input: readInput,
			CapabilityToken: token,
		})
		if err != nil {
			t.Fatalf("computer.uia.read_text: %v", err)
		}
		var readOut struct {
			Available bool `json:"available"`
			HWND      uint64 `json:"hwnd"`
			Payload   any  `json:"payload"`
		}
		if err := json.Unmarshal(readText.Output, &readOut); err != nil {
			t.Fatal(err)
		}
		if !readOut.Available || readOut.HWND != fgHwnd {
			t.Fatalf("read_text out=%#v", readOut)
		}

		actionInput, err := json.Marshal(map[string]any{
			"hwnd": fgHwnd, "name": "__remedy_no_such_control__", "role": "button", "action": "invoke",
		})
		if err != nil {
			t.Fatal(err)
		}
		action, err := registry.Execute(context.Background(), Request{
			ToolID: "computer.uia.action", Version: 1, Input: actionInput,
			CapabilityToken: token,
		})
		if err != nil {
			t.Fatalf("computer.uia.action: %v", err)
		}
		var actionOut struct {
			OK      bool   `json:"ok"`
			Message string `json:"message"`
			HWND    uint64 `json:"hwnd"`
			Action  string `json:"action"`
		}
		if err := json.Unmarshal(action.Output, &actionOut); err != nil {
			t.Fatal(err)
		}
		if actionOut.OK || actionOut.HWND != fgHwnd || actionOut.Action != "invoke" {
			t.Fatalf("expected not-found action result: %#v", actionOut)
		}
		if !strings.Contains(actionOut.Message, "not found") {
			t.Fatalf("expected not-found message: %#v", actionOut)
		}
	}

	clipFiles, err := registry.Execute(context.Background(), Request{
		ToolID: "clipboard.read_files", Version: 1, Input: json.RawMessage(`{}`),
		CapabilityToken: token,
	})
	if err != nil {
		t.Fatalf("clipboard.read_files: %v", err)
	}
	var filesOut struct {
		Files []string `json:"files"`
		Count int      `json:"count"`
	}
	if err := json.Unmarshal(clipFiles.Output, &filesOut); err != nil {
		t.Fatal(err)
	}
	if filesOut.Count != len(filesOut.Files) {
		t.Fatalf("files count mismatch: %#v", filesOut)
	}
	if filesOut.Files == nil {
		t.Fatal("expected non-nil files array")
	}

	clipImage, err := registry.Execute(context.Background(), Request{
		ToolID: "clipboard.read_image", Version: 1, Input: json.RawMessage(`{"label":"tool-abi"}`),
		CapabilityToken: token,
	})
	if err != nil {
		t.Fatalf("clipboard.read_image: %v", err)
	}
	var imageOut struct {
		Available bool   `json:"available"`
		Path      string `json:"path"`
		Bytes     int    `json:"bytes"`
	}
	if err := json.Unmarshal(clipImage.Output, &imageOut); err != nil {
		t.Fatal(err)
	}
	if imageOut.Available {
		if imageOut.Bytes < 1 || imageOut.Path == "" {
			t.Fatalf("available image missing path/bytes: %#v", imageOut)
		}
		if _, err := os.Stat(imageOut.Path); err != nil {
			t.Fatalf("clipboard png missing: %v", err)
		}
		if !strings.Contains(filepath.ToSlash(imageOut.Path), "/computer/clipboard/") {
			t.Fatalf("unexpected clipboard path %q", imageOut.Path)
		}
	} else if imageOut.Path != "" || imageOut.Bytes != 0 {
		t.Fatalf("unavailable image should be empty: %#v", imageOut)
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
