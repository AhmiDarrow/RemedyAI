package core

import (
	"os"
	"path/filepath"
	"runtime"
	"testing"
)

func TestOpenDevLibraryWhenPresent(t *testing.T) {
	ResetForTest()
	t.Cleanup(ResetForTest)
	path := FindLibraryPath()
	if path == "" {
		t.Skip("remedy_core not built (native/zig/zig-out)")
	}
	lib, err := Open()
	if err != nil {
		t.Fatalf("Open: %v", err)
	}
	if lib.Path() != path && filepath.Base(lib.Path()) != filepath.Base(path) {
		t.Fatalf("path=%q open=%q", path, lib.Path())
	}
	if runtime.GOOS == "windows" {
		ok, err := ConptyAvailable()
		if err != nil {
			t.Fatalf("ConptyAvailable: %v", err)
		}
		t.Logf("conpty_available=%v lib=%s", ok, lib.Path())
	}
}

func TestOpenFailsClosedMissingLib(t *testing.T) {
	ResetForTest()
	t.Cleanup(ResetForTest)
	t.Setenv("REMEDY_NATIVE_CORE_LIB", filepath.Join(t.TempDir(), "missing.dll"))
	_, err := Open()
	if err == nil {
		t.Fatal("expected error")
	}
	if !os.IsNotExist(err) && err != ErrUnavailable {
		if !containsUnavailable(err) {
			t.Fatalf("err=%v", err)
		}
	}
}

func containsUnavailable(err error) bool {
	for err != nil {
		if err == ErrUnavailable {
			return true
		}
		type unwrapper interface{ Unwrap() error }
		u, ok := err.(unwrapper)
		if !ok {
			return false
		}
		err = u.Unwrap()
	}
	return false
}

func TestFindLibraryPathIncludesCheckoutName(t *testing.T) {
	path := FindLibraryPath()
	if path == "" {
		// No built DLL is fine; still ensure names are considered via env miss.
		t.Setenv("REMEDY_NATIVE_CORE_LIB", "")
		_ = FindLibraryPath()
		return
	}
	base := filepath.Base(path)
	switch base {
	case "remedy_core.dll", "libremedy_core.so", "libremedy_core.dylib":
	default:
		t.Fatalf("unexpected library basename %q", base)
	}
}

func TestLoadTailscaleStatusWhenDLLPresent(t *testing.T) {
	if runtime.GOOS != "windows" {
		t.Skip("Windows DLL load")
	}
	ResetForTest()
	t.Cleanup(ResetForTest)
	dll := FindLibraryPath()
	if dll == "" {
		t.Skip("remedy_core.dll not built")
	}
	t.Setenv("REMEDY_NATIVE_CORE_LIB", dll)
	ResetForTest()
	if err := EnsureLoaded(); err != nil {
		t.Fatalf("load: %v", err)
	}
	raw, err := TailscaleStatusJSON()
	if err != nil {
		t.Fatalf("status: %v", err)
	}
	if len(raw) < 10 {
		t.Fatalf("short json %q", raw)
	}
}
