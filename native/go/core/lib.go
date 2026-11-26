package core

import (
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"sync"
)

var (
	libMu    sync.Mutex
	libCache *Library
	libPath  string
	libErr   error
	libTried bool
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
// The dev zig-out is only ever the checkout this code was built from or the
// one the executable sits in (devCheckoutRoots), never a folder found by
// walking up from the working directory: a library planted in a parent of
// wherever Remedy was started would otherwise be loaded.
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
		dir := filepath.Dir(exe)
		if p := add(dir); p != "" {
			packaged = p
		} else if p := add(filepath.Join(dir, "bin")); p != "" {
			packaged = p
		} else if runtime.GOOS == "darwin" {
			// Tauri places bundle.resources in Contents/Resources, while the
			// sidecar lives in Contents/MacOS. Look there before giving up.
			if p := add(filepath.Join(dir, "..", "Resources")); p != "" {
				packaged = p
			} else if p := add(filepath.Join(dir, "..", "Resources", "bin")); p != "" {
				packaged = p
			}
		}
	}
	var dev string
	for _, repo := range devCheckoutRoots() {
		if p := add(filepath.Join(repo, "native", "zig", "zig-out", "bin")); p != "" {
			dev = p
			break
		}
		if p := add(filepath.Join(repo, "native", "zig", "zig-out", "lib")); p != "" {
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

// devCheckoutRoots are the source checkouts whose native/zig/zig-out may hold
// a dev build: the one this file was compiled from (go test / go run; a
// -trimpath release build records no absolute path, so it has none) and the
// one a dev executable sits in (desktop/bin, desktop/src-tauri/target/...).
func devCheckoutRoots() []string {
	var roots []string
	if _, file, _, ok := runtime.Caller(0); ok && filepath.IsAbs(file) {
		// <repo>/native/go/core/lib.go
		roots = append(roots, filepath.Dir(filepath.Dir(filepath.Dir(filepath.Dir(file)))))
	}
	if exe, err := os.Executable(); err == nil {
		if repo := checkoutOfExecutable(filepath.Dir(exe)); repo != "" {
			roots = append(roots, repo)
		}
	}
	return roots
}

// checkoutOfExecutable is the repo root when dir is a dev output folder of a
// checkout: <repo>/desktop/bin or <repo>/desktop/src-tauri/target/[<triple>/]<profile>.
func checkoutOfExecutable(dir string) string {
	dir = filepath.Clean(dir)
	parent := filepath.Dir(dir)
	if filepath.Base(dir) == "bin" && filepath.Base(parent) == "desktop" {
		return filepath.Dir(parent)
	}
	for d, i := dir, 0; i < 3 && d != filepath.Dir(d); d, i = filepath.Dir(d), i+1 {
		srcTauri := filepath.Dir(d)
		desktop := filepath.Dir(srcTauri)
		if filepath.Base(d) == "target" && filepath.Base(srcTauri) == "src-tauri" && filepath.Base(desktop) == "desktop" {
			return filepath.Dir(desktop)
		}
	}
	return ""
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

// ResetForTest clears the library cache (tests only). The module itself is
// deliberately left loaded: an in-flight call on another goroutine would
// otherwise execute in unmapped memory once the handle is released.
func ResetForTest() {
	libMu.Lock()
	defer libMu.Unlock()
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
