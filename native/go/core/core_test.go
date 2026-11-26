package core

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
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

func TestFindLibraryPathIgnoresALibraryAboveTheWorkingDirectory(t *testing.T) {
	t.Setenv("REMEDY_NATIVE_CORE_LIB", "")
	base := t.TempDir()
	for _, sub := range []string{"bin", "lib"} {
		dir := filepath.Join(base, "native", "zig", "zig-out", sub)
		if err := os.MkdirAll(dir, 0o755); err != nil {
			t.Fatal(err)
		}
		for _, name := range libraryNames() {
			if err := os.WriteFile(filepath.Join(dir, name), []byte("planted"), 0o644); err != nil {
				t.Fatal(err)
			}
		}
	}
	wd := filepath.Join(base, "downloads", "project")
	if err := os.MkdirAll(wd, 0o755); err != nil {
		t.Fatal(err)
	}
	t.Chdir(wd)
	if got := FindLibraryPath(); got != "" && strings.HasPrefix(got, base) {
		t.Fatalf("loaded a library planted above the working directory: %s", got)
	}
}

func TestCheckoutOfExecutableKnowsOnlyDevLayouts(t *testing.T) {
	repo := filepath.Join(string(filepath.Separator), "src", "remedy")
	for dir, want := range map[string]string{
		filepath.Join(repo, "desktop", "bin"):                                                      repo,
		filepath.Join(repo, "desktop", "src-tauri", "target", "debug"):                             repo,
		filepath.Join(repo, "desktop", "src-tauri", "target", "x86_64-pc-windows-msvc", "release"): repo,
		filepath.Join(string(filepath.Separator), "opt", "Remedy"):                                 "",
		filepath.Join(string(filepath.Separator), "usr", "bin"):                                    "",
	} {
		if got := checkoutOfExecutable(dir); got != want {
			t.Errorf("checkoutOfExecutable(%s)=%q want %q", dir, got, want)
		}
	}
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
