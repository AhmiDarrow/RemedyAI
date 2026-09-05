package core

import (
	"encoding/json"
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestClipboardRichWrappersFailClosedWithoutLibrary(t *testing.T) {
	ResetForTest()
	t.Cleanup(ResetForTest)
	t.Setenv("REMEDY_NATIVE_CORE_LIB", filepath.Join(t.TempDir(), "missing.dll"))

	if _, err := ClipboardGetFiles(); err == nil {
		t.Fatal("ClipboardGetFiles expected fail-closed")
	}
	if _, err := ClipboardGetImagePNG(); err == nil {
		t.Fatal("ClipboardGetImagePNG expected fail-closed")
	}
	if _, err := ForegroundDetailJSON(); err == nil {
		t.Fatal("ForegroundDetailJSON expected fail-closed")
	}
}

func TestClipboardRichWrappersLiveWhenLibraryPresent(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows clipboard rich surface")
	}
	ResetForTest()
	t.Cleanup(ResetForTest)
	if FindLibraryPath() == "" {
		t.Skip("remedy_core.dll not built")
	}

	files, err := ClipboardGetFiles()
	if err != nil {
		t.Fatalf("ClipboardGetFiles: %v", err)
	}
	if files == nil {
		t.Fatal("expected non-nil files slice")
	}

	png, err := ClipboardGetImagePNG()
	if err != nil {
		t.Fatalf("ClipboardGetImagePNG: %v", err)
	}
	if png == nil {
		t.Fatal("expected non-nil png slice")
	}

	raw, err := ForegroundDetailJSON()
	if err != nil {
		t.Fatalf("ForegroundDetailJSON: %v", err)
	}
	if len(raw) == 0 {
		t.Fatal("expected non-empty foreground JSON")
	}
	var detail map[string]any
	if err := json.Unmarshal(raw, &detail); err != nil {
		t.Fatalf("decode foreground: %v", err)
	}
	for _, key := range []string{"hwnd", "title", "pid", "exe"} {
		if _, ok := detail[key]; !ok {
			t.Fatalf("missing %s in %#v", key, detail)
		}
	}
}

func TestWriteClipboardPNG(t *testing.T) {
	home := t.TempDir()
	path, err := WriteClipboardPNG(home, "probe", []byte{0x89, 0x50, 0x4e, 0x47})
	if err != nil {
		t.Fatal(err)
	}
	raw, err := os.ReadFile(path)
	if err != nil {
		t.Fatal(err)
	}
	if len(raw) != 4 {
		t.Fatalf("bytes=%d", len(raw))
	}
	if filepath.Base(filepath.Dir(path)) != "clipboard" {
		t.Fatalf("unexpected dir: %s", path)
	}
}
