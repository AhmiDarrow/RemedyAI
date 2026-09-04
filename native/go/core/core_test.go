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
		// wrapped ErrUnavailable is fine
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
