package core

import (
	"path/filepath"
	"runtime"
	"testing"
)

func TestUIAWrappersFailClosedWithoutLibrary(t *testing.T) {
	ResetForTest()
	t.Cleanup(ResetForTest)
	t.Setenv("REMEDY_NATIVE_CORE_LIB", filepath.Join(t.TempDir(), "missing.dll"))

	if _, err := UIAFocusedElementJSON(); err == nil {
		t.Fatal("UIAFocusedElementJSON expected fail-closed")
	}
	if _, err := UIAReadWindowTextJSON(1, 100); err == nil {
		t.Fatal("UIAReadWindowTextJSON expected fail-closed")
	}
	if _, err := UIAElementActionJSON(1, "OK", "button", "invoke", ""); err == nil {
		t.Fatal("UIAElementActionJSON expected fail-closed")
	}
}

func TestUIAWrappersLiveWhenLibraryPresent(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows UIA surface")
	}
	ResetForTest()
	t.Cleanup(ResetForTest)
	if FindLibraryPath() == "" {
		t.Skip("remedy_core.dll not built")
	}

	ok, err := UIAAvailable()
	if err != nil {
		t.Fatalf("UIAAvailable: %v", err)
	}
	if !ok {
		t.Skip("UIA unavailable on this thread")
	}

	focused, err := UIAFocusedElementJSON()
	if err != nil {
		t.Fatalf("UIAFocusedElementJSON: %v", err)
	}
	if len(focused) == 0 {
		t.Fatal("expected JSON bytes (object or null)")
	}

	// hwnd 0 → desktop root; may return null JSON when nothing useful is found.
	raw, err := UIAReadWindowTextJSON(0, 500)
	if err != nil {
		t.Fatalf("UIAReadWindowTextJSON: %v", err)
	}
	if len(raw) == 0 {
		t.Fatal("expected JSON bytes (object or null)")
	}

	action, err := UIAElementActionJSON(1, "__remedy_no_such__", "button", "invoke", "")
	if err != nil {
		t.Fatalf("UIAElementActionJSON: %v", err)
	}
	if len(action) == 0 {
		t.Fatal("expected action JSON object")
	}
}
