package tools

import (
	"errors"
	"fmt"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

// errLookPathDot is the same policy as exec.ErrDot: a bare name found only in
// the current directory is not an authorized PATH hit.
var errLookPathDot = errors.New("cannot run executable found relative to current directory")

func errLookPathNotFound(file string) error {
	return fmt.Errorf("%s: executable file not found in $PATH", file)
}

// lookPath resolves a program the way exec.LookPath does, without importing
// os/exec (forbidden in this package). Bare names search PATH and skip "." so
// a planted binary in the project cannot shadow a system tool.
func lookPath(file string) (string, error) {
	if file == "" {
		return "", errLookPathNotFound(file)
	}
	if filepath.IsAbs(file) || strings.ContainsAny(file, `/\`) {
		if isRunnable(file) {
			return file, nil
		}
		return "", errLookPathNotFound(file)
	}
	pathEnv := os.Getenv("PATH")
	foundInDot := false
	for _, dir := range filepath.SplitList(pathEnv) {
		if dir == "" {
			dir = "."
		}
		for _, cand := range pathCandidates(dir, file) {
			if !isRunnable(cand) {
				continue
			}
			if dir == "." {
				foundInDot = true
				continue
			}
			return cand, nil
		}
	}
	if foundInDot {
		return "", errLookPathDot
	}
	return "", errLookPathNotFound(file)
}

func pathCandidates(dir, name string) []string {
	if runtime.GOOS != "windows" {
		return []string{filepath.Join(dir, name)}
	}
	exts := windowsPathExts()
	lower := strings.ToLower(name)
	for _, ext := range exts {
		if ext != "" && strings.HasSuffix(lower, strings.ToLower(ext)) {
			return []string{filepath.Join(dir, name)}
		}
	}
	out := make([]string, 0, len(exts))
	for _, ext := range exts {
		out = append(out, filepath.Join(dir, name+ext))
	}
	return out
}

func windowsPathExts() []string {
	pathExt := os.Getenv("PATHEXT")
	if pathExt == "" {
		pathExt = ".COM;.EXE;.BAT;.CMD"
	}
	parts := strings.Split(pathExt, ";")
	out := make([]string, 0, len(parts))
	for _, p := range parts {
		p = strings.TrimSpace(p)
		if p == "" {
			continue
		}
		out = append(out, p)
	}
	if len(out) == 0 {
		return []string{".exe"}
	}
	return out
}

func isRunnable(path string) bool {
	st, err := os.Stat(path)
	if err != nil || st.IsDir() {
		return false
	}
	if runtime.GOOS == "windows" {
		return true
	}
	return st.Mode()&0o111 != 0
}
