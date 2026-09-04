package httpapi

import (
	"errors"
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

var errPathOutside = errors.New("path outside allowed directory")

var filesSearchSkipDirs = map[string]struct{}{
	".git": {}, "__pycache__": {}, "node_modules": {}, ".venv": {}, "venv": {},
	"dist": {}, "build": {}, ".tox": {}, ".mypy_cache": {}, ".pytest_cache": {},
	"AppData": {}, "Library": {}, "Caches": {}, ".cache": {}, "target": {},
}

var blockedSearchParts = map[string]struct{}{
	"windows": {}, "program files": {}, "program files (x86)": {},
	"programdata": {}, "system32": {}, "syswow64": {},
}

func isVolumeRootPath(raw string) bool {
	text := strings.TrimSpace(raw)
	if text == "" {
		return false
	}
	if text == "/" || text == `\` {
		return true
	}
	// Windows drive root: C: / C:\ / C:/
	if len(text) <= 3 && len(text) >= 2 && isDriveLetter(text[0]) && text[1] == ':' {
		rest := strings.Trim(text[2:], `/\`)
		return rest == ""
	}
	abs, err := filepath.Abs(text)
	if err != nil {
		return false
	}
	abs = filepath.Clean(abs)
	return filepath.Dir(abs) == abs
}

func isDriveLetter(b byte) bool {
	return (b >= 'A' && b <= 'Z') || (b >= 'a' && b <= 'z')
}

func isUnsetProjectPath(raw string) bool {
	text := strings.TrimSpace(raw)
	return text == "" || text == "." || text == "./" || isVolumeRootPath(text)
}

func normalizeAccessScope(raw string) string {
	s := strings.ToLower(strings.TrimSpace(raw))
	switch s {
	case "home", "user", "project+home", "project_home":
		return "home"
	case "full", "machine", "all", "unrestricted":
		return "full"
	case "untrusted", "sandbox", "strict", "download":
		return "untrusted"
	default:
		return "project"
	}
}

func effectiveAccessScope(configured, projectRaw string) string {
	if isUnsetProjectPath(projectRaw) {
		return "full"
	}
	return normalizeAccessScope(configured)
}

func resolveAbsPath(raw string) (string, error) {
	text := strings.TrimSpace(raw)
	if text == "" {
		return "", errors.New("empty path")
	}
	if strings.HasPrefix(text, "~") {
		home, err := os.UserHomeDir()
		if err != nil {
			return "", err
		}
		if text == "~" {
			text = home
		} else if strings.HasPrefix(text, "~/") || strings.HasPrefix(text, `~\`) {
			text = filepath.Join(home, text[2:])
		}
	}
	abs, err := filepath.Abs(text)
	if err != nil {
		return "", err
	}
	return filepath.Clean(abs), nil
}

func clampFilesBase(base string) string {
	resolved, err := resolveAbsPath(base)
	if err != nil || resolved == "" {
		if h, e := os.UserHomeDir(); e == nil {
			return h
		}
		return base
	}
	if filepath.Dir(resolved) == resolved {
		if h, e := os.UserHomeDir(); e == nil {
			if abs, e2 := filepath.Abs(h); e2 == nil {
				return filepath.Clean(abs)
			}
			return h
		}
	}
	return resolved
}

func ensureDir(path string) (string, error) {
	abs, err := resolveAbsPath(path)
	if err != nil {
		return "", err
	}
	st, err := os.Stat(abs)
	if err != nil {
		if os.IsNotExist(err) {
			if mkErr := os.MkdirAll(abs, 0o700); mkErr != nil {
				return "", mkErr
			}
			return abs, nil
		}
		return "", err
	}
	if !st.IsDir() {
		return "", errors.New("project path is not a directory")
	}
	return abs, nil
}

// isProtectedSecretPath refuses ~/.remedy/auth and $REMEDY_HOME/auth trees.
func isProtectedSecretPath(path, homeDir string) bool {
	abs, err := resolveAbsPath(path)
	if err != nil || abs == "" {
		return true // fail closed
	}
	parts := splitPathParts(abs)
	for i := 0; i+1 < len(parts); i++ {
		if strings.EqualFold(parts[i], ".remedy") && strings.EqualFold(parts[i+1], "auth") {
			return true
		}
	}
	roots := make([]string, 0, 3)
	if h := strings.TrimSpace(homeDir); h != "" {
		roots = append(roots, filepath.Join(h, "auth"))
	}
	if env := strings.TrimSpace(os.Getenv("REMEDY_HOME")); env != "" {
		roots = append(roots, filepath.Join(env, "auth"))
	}
	if uh, err := os.UserHomeDir(); err == nil && uh != "" {
		roots = append(roots, filepath.Join(uh, ".remedy", "auth"))
	}
	for _, root := range roots {
		rabs, err := resolveAbsPath(root)
		if err != nil {
			continue
		}
		if pathUnder(abs, rabs) {
			return true
		}
	}
	return false
}

func splitPathParts(path string) []string {
	cleaned := filepath.Clean(path)
	vol := filepath.VolumeName(cleaned)
	rest := cleaned
	if vol != "" {
		rest = cleaned[len(vol):]
	}
	rest = strings.Trim(rest, `/\`)
	if rest == "" {
		if vol != "" {
			return []string{vol}
		}
		return nil
	}
	parts := strings.FieldsFunc(rest, func(r rune) bool {
		return r == '/' || r == '\\'
	})
	if vol != "" {
		out := make([]string, 0, len(parts)+1)
		out = append(out, vol)
		out = append(out, parts...)
		return out
	}
	return parts
}

func jailPath(userPath, base string) (string, error) {
	baseAbs, err := resolveAbsPath(base)
	if err != nil {
		return "", err
	}
	raw := strings.TrimSpace(userPath)
	if raw == "" || raw == "." || raw == "./" {
		if isProtectedSecretPath(baseAbs, "") {
			return "", errPathOutside
		}
		return baseAbs, nil
	}
	candidate := raw
	if !filepath.IsAbs(raw) && !(runtime.GOOS == "windows" && len(raw) >= 2 && raw[1] == ':' && isDriveLetter(raw[0])) {
		candidate = filepath.Join(baseAbs, raw)
	}
	abs, err := resolveAbsPath(candidate)
	if err != nil {
		return "", err
	}
	if isProtectedSecretPath(abs, "") {
		return "", errPathOutside
	}
	if !pathUnder(abs, baseAbs) {
		return "", errPathOutside
	}
	return abs, nil
}

func filesEnvRoot() string {
	if v := strings.TrimSpace(os.Getenv("REMEDY_FILES_ROOT")); v != "" {
		return v
	}
	return strings.TrimSpace(os.Getenv("REMEDY_PROJECT_PATH"))
}

func userProfileWorkFolders(home string) []string {
	if home == "" {
		var err error
		home, err = os.UserHomeDir()
		if err != nil || home == "" {
			return nil
		}
	}
	homeAbs, err := resolveAbsPath(home)
	if err != nil {
		return nil
	}
	out := make([]string, 0, 3)
	for _, name := range []string{"Desktop", "Documents", "Downloads"} {
		p := filepath.Join(homeAbs, name)
		if st, err := os.Stat(p); err == nil && st.IsDir() {
			if abs, err := resolveAbsPath(p); err == nil {
				out = append(out, abs)
			}
		}
	}
	return out
}

func allowedReadRoots(scope, project string, userHome string) []string {
	proj := clampFilesBase(project)
	roots := []string{proj}
	scope = normalizeAccessScope(scope)
	if scope != "untrusted" {
		for _, f := range userProfileWorkFolders(userHome) {
			if !containsPath(roots, f) {
				roots = append(roots, f)
			}
		}
	}
	if scope == "home" || scope == "full" {
		if h, err := resolveAbsPath(userHome); err == nil && h != "" && !containsPath(roots, h) {
			roots = append(roots, h)
		}
	}
	return roots
}

func containsPath(roots []string, p string) bool {
	for _, r := range roots {
		if strings.EqualFold(r, p) {
			return true
		}
	}
	return false
}

func resolveUnderRoots(userPath string, roots []string, scope string) (string, error) {
	scope = normalizeAccessScope(scope)
	if len(roots) == 0 {
		return "", errPathOutside
	}
	primary := roots[0]
	raw := strings.TrimSpace(userPath)
	if raw == "" || raw == "." || raw == "./" {
		if isProtectedSecretPath(primary, "") {
			return "", errPathOutside
		}
		return primary, nil
	}

	isAbs := filepath.IsAbs(raw) ||
		(runtime.GOOS == "windows" && len(raw) >= 2 && raw[1] == ':' && isDriveLetter(raw[0]))

	if isAbs {
		abs, err := resolveAbsPath(raw)
		if err != nil {
			return "", err
		}
		if isProtectedSecretPath(abs, "") {
			return "", errPathOutside
		}
		if scope == "full" {
			parts := splitPathParts(abs)
			for _, p := range parts {
				low := strings.ToLower(p)
				if low == "$recycle.bin" || low == "system volume information" {
					return "", errPathOutside
				}
			}
			return abs, nil
		}
		for _, root := range roots {
			if pathUnder(abs, root) {
				return abs, nil
			}
		}
		return "", errPathOutside
	}

	// Relative
	if scope == "full" {
		abs, err := resolveAbsPath(filepath.Join(primary, raw))
		if err != nil {
			return "", err
		}
		if isProtectedSecretPath(abs, "") {
			return "", errPathOutside
		}
		return abs, nil
	}
	var lastErr error
	for _, root := range roots {
		abs, err := jailPath(raw, root)
		if err != nil {
			lastErr = err
			continue
		}
		return abs, nil
	}
	if lastErr != nil {
		return "", lastErr
	}
	return "", errPathOutside
}

func hasBlockedSearchPart(abs string) bool {
	for _, p := range splitPathParts(abs) {
		if _, ok := blockedSearchParts[strings.ToLower(p)]; ok {
			return true
		}
	}
	return false
}
