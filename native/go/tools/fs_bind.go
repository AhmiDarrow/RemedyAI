package tools

import (
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"runtime"
	"strings"
)

// isFullAccessScope is the access_scope that lifts the workspace-root fence
// for file tools. The runtime sets this when the owner chose full, and also
// when no project folder is selected (a warning plus free reign). Relative
// paths still resolve against the bound root; absolute paths may land
// anywhere except auth secrets.
func isFullAccessScope(scope string) bool {
	switch strings.ToLower(strings.TrimSpace(scope)) {
	case "full", "machine", "all", "unrestricted":
		return true
	default:
		return false
	}
}

func isSecretAuthPath(abs string) bool {
	cleaned := filepath.Clean(abs)
	parts := strings.FieldsFunc(cleaned, func(r rune) bool {
		return r == '/' || r == '\\'
	})
	for i := 0; i+1 < len(parts); i++ {
		if isRemedyDirName(parts[i]) && strings.EqualFold(winName(parts[i+1]), "auth") {
			return true
		}
	}
	return false
}

// winName is a path step as Windows opens it: without a stream suffix
// ("auth::$INDEX_ALLOCATION") and without the trailing dots and spaces it drops.
func winName(part string) string {
	if i := strings.IndexByte(part, ':'); i > 0 {
		part = part[:i]
	}
	return strings.TrimRight(part, ". ")
}

// isRemedyDirName reports ".remedy" in any spelling that opens it, its short
// 8.3 name included (REMEDY~1: the leading dot is dropped there).
func isRemedyDirName(part string) bool {
	n := strings.ToLower(winName(part))
	return n == ".remedy" || strings.HasPrefix(n, "remedy~")
}

// located is a path resolved for a file tool: either a path relative to the
// bound os.Root, or a host-absolute path when access_scope=full allows work
// outside that root.
type located struct {
	rel     string
	hostAbs string
	shown   string
	outside bool
}

func (l located) usesHost() bool { return l.hostAbs != "" }

// boundFS is the session's file jail plus the access scope that can lift it.
type boundFS struct {
	root    *os.Root
	rootAbs string
	scope   string
}

func openBoundFS(workspaceRoot, scope string) (*boundFS, error) {
	root, rootAbs, err := boundRoot(workspaceRoot)
	if err != nil {
		return nil, err
	}
	return &boundFS{root: root, rootAbs: rootAbs, scope: scope}, nil
}

func (b *boundFS) Close() {
	if b != nil && b.root != nil {
		_ = b.root.Close()
	}
}

func (b *boundFS) locate(p string) (located, error) {
	return b.locateOS(p, userHomeDir(), hostPathOS())
}

func (b *boundFS) locateOS(p, home string, osName PathOS) (located, error) {
	if b == nil {
		return located{}, fmt.Errorf("%w: no workspace root is bound for this turn", ErrInvalidInput)
	}
	rel, err := resolveBoundPathOS(b.rootAbs, p, home, osName)
	if err == nil {
		// Inside the bound folder is not enough when that folder is the home
		// folder (or .remedy itself): the secrets are inside it too.
		if isSecretAuthPath(filepath.Join(b.rootAbs, rel)) {
			return located{}, fmt.Errorf("%w: %q is an auth secret path and cannot be opened", ErrInvalidInput, p)
		}
		shown := filepath.ToSlash(filepath.Join(b.rootAbs, rel))
		return located{rel: rel, shown: shown}, nil
	}
	if !isFullAccessScope(b.scope) || !isOutsideRootError(err) {
		return located{}, err
	}
	native := normalizeInputPath(strings.TrimSpace(p), home, osName)
	if !isAbsPath(native, osName) {
		return located{}, err
	}
	abs := native
	if osName != hostPathOS() {
		// Cross-OS tests resolve the display path only; they never open it.
		abs = native
	} else {
		var resErr error
		abs, resErr = filepath.Abs(native)
		if resErr != nil {
			return located{}, fmt.Errorf("%w: path %q is not usable: %v", ErrInvalidInput, p, resErr)
		}
		abs = filepath.Clean(abs)
	}
	if isSecretAuthPath(abs) {
		return located{}, fmt.Errorf("%w: %q is an auth secret path and cannot be opened, even with full access", ErrInvalidInput, p)
	}
	return located{
		hostAbs: abs,
		shown:   filepath.ToSlash(abs),
		outside: true,
	}, nil
}

func isOutsideRootError(err error) bool {
	return err != nil && strings.Contains(err.Error(), "outside the workspace root")
}

func (b *boundFS) Stat(loc located) (fs.FileInfo, error) {
	if loc.usesHost() {
		return os.Stat(loc.hostAbs)
	}
	return b.root.Stat(loc.rel)
}

func (b *boundFS) ReadFile(loc located) ([]byte, error) {
	if loc.usesHost() {
		return os.ReadFile(loc.hostAbs)
	}
	return b.root.ReadFile(loc.rel)
}

func (b *boundFS) MkdirAll(loc located, _ string, perm os.FileMode) error {
	if loc.usesHost() {
		return os.MkdirAll(filepath.Dir(loc.hostAbs), perm)
	}
	parent := path.Dir(loc.rel)
	if parent == "." || parent == "" {
		return nil
	}
	return b.root.MkdirAll(parent, perm)
}

func (b *boundFS) atomicWrite(loc located, data []byte) error {
	if loc.usesHost() {
		return atomicWriteHost(loc.hostAbs, data)
	}
	return atomicWriteInRoot(b.root, loc.rel, data)
}

func (b *boundFS) searchBase(p string) (located, error) {
	if strings.TrimSpace(p) == "" {
		return located{rel: ".", shown: filepath.ToSlash(b.rootAbs)}, nil
	}
	loc, err := b.locate(p)
	if err != nil {
		return located{}, err
	}
	if _, err := b.Stat(loc); err != nil {
		return located{}, statError("search", p, b.rootAbs, err)
	}
	return loc, nil
}

// atomicWriteHost writes through a sibling temp file and renames over the
// target, matching atomicWriteInRoot for paths opened outside the jail.
func atomicWriteHost(abs string, data []byte) error {
	dir := filepath.Dir(abs)
	if err := os.MkdirAll(dir, 0o755); err != nil {
		return fmt.Errorf("%w: could not create %q: %v", ErrInvalidInput, dir, err)
	}
	base := filepath.Base(abs)
	var tmp string
	var file *os.File
	for attempt := 0; attempt < 8; attempt++ {
		name := fmt.Sprintf(".%s.remedy-%d.tmp", base, os.Getpid()+attempt*7919)
		path := filepath.Join(dir, name)
		f, err := os.OpenFile(path, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err == nil {
			tmp, file = path, f
			break
		}
		if !errors.Is(err, os.ErrExist) {
			return fmt.Errorf("%w: could not create a temporary file beside %q: %v", ErrInvalidInput, abs, err)
		}
	}
	if file == nil {
		return fmt.Errorf("%w: could not create a temporary file beside %q", ErrInvalidInput, abs)
	}
	var keepMode os.FileMode
	if info, err := os.Stat(abs); err == nil && info.Mode().IsRegular() {
		keepMode = info.Mode().Perm()
	}
	writeErr := func() error {
		defer file.Close()
		if keepMode != 0 {
			if err := file.Chmod(keepMode); err != nil && runtime.GOOS != "windows" {
				return err
			}
		}
		if _, err := file.Write(data); err != nil {
			return err
		}
		return file.Sync()
	}()
	if writeErr != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("%w: writing %q failed: %v", ErrInvalidInput, abs, writeErr)
	}
	if err := os.Rename(tmp, abs); err != nil {
		_ = os.Remove(tmp)
		return fmt.Errorf("%w: replacing %q failed: %v", ErrInvalidInput, abs, err)
	}
	return nil
}

// fullScopeNote is attached to a successful file-tool result when the path
// was outside the bound folder under access_scope=full (no project, or the
// owner chose full access).
func fullScopeNote() string {
	return "No project folder is selected (or access_scope is full), so file tools have free reign on this machine except auth secrets. Choose a project folder to limit them."
}

// walkHost walks a host directory the same way walkBound walks a jailed tree.
func walkHost(ctx interface{ Err() error }, base, pattern string, visit func(rel, abs string, d fs.DirEntry) error) error {
	err := filepath.WalkDir(base, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			if d != nil && d.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		if ctx != nil && ctx.Err() != nil {
			return ctx.Err()
		}
		rel, relErr := filepath.Rel(base, p)
		if relErr != nil {
			return nil
		}
		rel = filepath.ToSlash(rel)
		if d.IsDir() {
			// A search never looks inside the secrets folder, wherever it started.
			if isSecretAuthPath(p) {
				return fs.SkipDir
			}
			if p != base && skipWalkDir(d.Name(), pattern) {
				return fs.SkipDir
			}
			return nil
		}
		if !d.Type().IsRegular() {
			return nil
		}
		return visit(rel, p, d)
	})
	if err != nil && !errors.Is(err, fs.SkipAll) {
		if ctx != nil && ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("%w: walking %q: %v", ErrInvalidInput, base, err)
	}
	return nil
}
