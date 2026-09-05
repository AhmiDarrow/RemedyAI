package core

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"
)

var (
	libMu     sync.Mutex
	libCache  *Library
	libPath   string
	libErr    error
	libTried  bool
)

// Library is a loaded remedy_core shared library at ABIVersion.
type Library struct {
	path string
	raw  nativeLib
}

func libraryNames() []string {
	switch runtime.GOOS {
	case "windows":
		return []string{"remedy_core.dll"}
	case "darwin":
		return []string{"libremedy_core.dylib"}
	default:
		return []string{"libremedy_core.so"}
	}
}

// FindLibraryPath mirrors Python native_runtime._core_library_path.
// When both a staged (exe-adjacent) copy and a fresher zig-out build exist,
// prefer zig-out so tauri:dev cannot bind a stale same-ABI DLL after zig build.
func FindLibraryPath() string {
	if explicit := os.Getenv("REMEDY_NATIVE_CORE_LIB"); explicit != "" {
		if st, err := os.Stat(explicit); err == nil && !st.IsDir() {
			return explicit
		}
		return ""
	}
	names := libraryNames()
	seen := map[string]struct{}{}
	add := func(dir string) string {
		for _, name := range names {
			p := filepath.Join(dir, name)
			if _, ok := seen[p]; ok {
				continue
			}
			seen[p] = struct{}{}
			if st, err := os.Stat(p); err == nil && !st.IsDir() {
				return p
			}
		}
		return ""
	}
	var packaged string
	if exe, err := os.Executable(); err == nil {
		if p := add(filepath.Dir(exe)); p != "" {
			packaged = p
		} else if p := add(filepath.Join(filepath.Dir(exe), "bin")); p != "" {
			packaged = p
		}
	}
	wd, err := os.Getwd()
	if err != nil {
		wd = ""
	}
	start := wd
	if start == "" {
		if exe, err := os.Executable(); err == nil {
			start = filepath.Dir(exe)
		}
	}
	var dev string
	for d := start; d != "" && d != filepath.Dir(d); d = filepath.Dir(d) {
		if p := add(filepath.Join(d, "native", "zig", "zig-out", "bin")); p != "" {
			dev = p
			break
		}
		if p := add(filepath.Join(d, "native", "zig", "zig-out", "lib")); p != "" {
			dev = p
			break
		}
		if p := add(filepath.Join(d, "zig", "zig-out", "bin")); p != "" {
			dev = p
			break
		}
		if p := add(filepath.Join(d, "zig", "zig-out", "lib")); p != "" {
			dev = p
			break
		}
	}
	if packaged != "" && dev != "" && packaged != dev {
		pst, perr := os.Stat(packaged)
		dst, derr := os.Stat(dev)
		if perr == nil && derr == nil && dst.ModTime().After(pst.ModTime()) {
			// Match Python native_runtime: warn so tauri:dev does not silently
			// look like it is still on the staged desktop/bin copy.
			fmt.Fprintf(
				os.Stderr,
				"remedy_core: preferring newer zig-out %s over staged %s\n",
				dev,
				packaged,
			)
			return dev
		}
		return packaged
	}
	if packaged != "" {
		return packaged
	}
	return dev
}

// Open loads remedy_core (cached). Fail-closed on missing / ABI mismatch.
func Open() (*Library, error) {
	libMu.Lock()
	defer libMu.Unlock()
	if libTried {
		return libCache, libErr
	}
	libTried = true
	path := FindLibraryPath()
	if path == "" {
		libErr = fmt.Errorf("%w: library not found (build native/zig or set REMEDY_NATIVE_CORE_LIB)", ErrUnavailable)
		return nil, libErr
	}
	raw, err := openNative(path)
	if err != nil {
		libErr = fmt.Errorf("%w: load %s: %v", ErrUnavailable, path, err)
		return nil, libErr
	}
	lib := &Library{path: path, raw: raw}
	abi, err := lib.abiVersion()
	if err != nil {
		_ = raw.close()
		libErr = fmt.Errorf("%w: abi probe: %v", ErrUnavailable, err)
		return nil, libErr
	}
	if abi != ABIVersion {
		_ = raw.close()
		libErr = fmt.Errorf("%w: ABI %d at %s; required %d", ErrUnavailable, abi, path, ABIVersion)
		return nil, libErr
	}
	libCache = lib
	libPath = path
	return libCache, nil
}

// ResetForTest clears the library cache (tests only).
func ResetForTest() {
	libMu.Lock()
	defer libMu.Unlock()
	if libCache != nil {
		_ = libCache.raw.close()
	}
	libCache = nil
	libPath = ""
	libErr = nil
	libTried = false
	signingReady = false
}

func (l *Library) Path() string {
	if l == nil {
		return ""
	}
	return l.path
}

func (l *Library) check(fn string, status int32) error {
	if status == StatusOK {
		return nil
	}
	osErr := uint32(0)
	if status == StatusOperationFailed {
		osErr = l.lastOSError()
	}
	return &HostError{Function: fn, Status: status, OSError: osErr}
}
