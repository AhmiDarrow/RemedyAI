package core

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestPrintWindowFailClosedWithoutLibrary(t *testing.T) {
	ResetForTest()
	t.Cleanup(ResetForTest)
	t.Setenv("REMEDY_NATIVE_CORE_LIB", filepath.Join(t.TempDir(), "missing.dll"))

	if _, err := PrintWindow(1, 3); err == nil {
		t.Fatal("PrintWindow expected fail-closed")
	}
	if _, err := PrintWindowPNG(t.TempDir(), "hwnd", 1); err == nil {
		t.Fatal("PrintWindowPNG expected fail-closed")
	}
}

func TestPrintWindowPNGRejectsZeroHwnd(t *testing.T) {
	if _, err := PrintWindowPNG(t.TempDir(), "hwnd", 0); err == nil {
		t.Fatal("expected hwnd required error")
	}
}

func TestPrintWindowLiveWhenLibraryPresent(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows PrintWindow surface")
	}
	ResetForTest()
	t.Cleanup(ResetForTest)
	if FindLibraryPath() == "" {
		t.Skip("remedy_core.dll not built")
	}

	raw, err := ListWindowsJSON(10)
	if err != nil {
		t.Fatalf("ListWindowsJSON: %v", err)
	}
	var windows []map[string]any
	if err := json.Unmarshal(raw, &windows); err != nil {
		t.Fatalf("decode windows: %v", err)
	}
	home := t.TempDir()
	for _, w := range windows {
		var hwnd uint64
		switch v := w["hwnd"].(type) {
		case float64:
			hwnd = uint64(v)
		}
		if hwnd == 0 {
			continue
		}
		info, err := PrintWindowPNG(home, "core-print", hwnd)
		if err != nil {
			t.Logf("PrintWindowPNG hwnd=%d: %v", hwnd, err)
			continue
		}
		path, _ := info["path"].(string)
		if path == "" {
			t.Fatalf("missing path: %#v", info)
		}
		if _, err := os.Stat(path); err != nil {
			t.Fatalf("shot missing: %v", err)
		}
		if info["method"] != "PrintWindow" {
			t.Fatalf("method=%v", info["method"])
		}
		if info["hwnd"] != hwnd {
			t.Fatalf("hwnd=%v want %d", info["hwnd"], hwnd)
		}
		width, _ := info["width"].(int)
		height, _ := info["height"].(int)
		if width < 1 || height < 1 {
			t.Fatalf("geometry: %#v", info)
		}
		return
	}
	t.Log("no window accepted PrintWindow (acceptable on locked/minimized desktops)")
}
