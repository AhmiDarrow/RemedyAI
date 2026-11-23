package tools

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
	"unicode"
)

// PathOS names the path conventions to apply. Tests pass "windows" or "unix"
// so drive-letter paths, backslashes and ~ can be checked on a Linux builder.
type PathOS string

const (
	PathOSWindows PathOS = "windows"
	PathOSUnix    PathOS = "unix"
)

func hostPathOS() PathOS {
	if runtime.GOOS == "windows" {
		return PathOSWindows
	}
	return PathOSUnix
}

// userHomeDir is the owner's home for ~ expansion. USERPROFILE wins on
// Windows; HOME is accepted on both.
func userHomeDir() string {
	if runtime.GOOS == "windows" {
		if v := strings.TrimSpace(os.Getenv("USERPROFILE")); v != "" {
			return v
		}
	}
	if v := strings.TrimSpace(os.Getenv("HOME")); v != "" {
		return v
	}
	h, _ := os.UserHomeDir()
	return h
}

func userHomeDirFor(osName PathOS, getenv func(string) string) string {
	if getenv == nil {
		getenv = os.Getenv
	}
	if osName == PathOSWindows {
		if v := strings.TrimSpace(getenv("USERPROFILE")); v != "" {
			return v
		}
	}
	if v := strings.TrimSpace(getenv("HOME")); v != "" {
		return v
	}
	return ""
}

// expandHome expands a leading ~ or ~/ or ~\ to home. Only the current
// user's home is expanded (~other stays literal).
func expandHome(p, home string, osName PathOS) string {
	p = strings.TrimSpace(p)
	if p == "" || home == "" {
		return p
	}
	if p == "~" {
		return home
	}
	if osName == PathOSWindows {
		if strings.HasPrefix(p, `~\`) || strings.HasPrefix(p, `~/`) {
			rest := strings.TrimLeft(p[2:], `/\`)
			if rest == "" {
				return home
			}
			return strings.TrimRight(home, `/\`) + `\` + strings.ReplaceAll(rest, "/", `\`)
		}
		return p
	}
	if strings.HasPrefix(p, "~/") {
		return strings.TrimRight(home, "/") + "/" + p[2:]
	}
	return p
}

// isAbsPath reports whether p is absolute under osName's rules. A Windows
// drive-letter or UNC path is absolute even when this process is running
// on Unix, so tests can exercise the Windows branch.
func isAbsPath(p string, osName PathOS) bool {
	p = strings.TrimSpace(p)
	if p == "" {
		return false
	}
	if osName == PathOSWindows {
		if isWindowsDriveAbs(p) {
			return true
		}
		// UNC \\server\share
		if strings.HasPrefix(p, `\\`) || strings.HasPrefix(p, "//") {
			return len(p) > 2
		}
		return false
	}
	return strings.HasPrefix(p, "/")
}

func isWindowsDriveAbs(p string) bool {
	if len(p) < 3 {
		return false
	}
	if !unicode.IsLetter(rune(p[0])) || p[1] != ':' {
		return false
	}
	return p[2] == '\\' || p[2] == '/'
}

// normalizeInputPath expands ~ and rewrites separators to the native form
// for osName, then cleans "." and ".." segments.
func normalizeInputPath(p, home string, osName PathOS) string {
	p = expandHome(strings.TrimSpace(p), home, osName)
	if p == "" {
		return p
	}
	if osName == PathOSWindows {
		return cleanWindowsPath(p)
	}
	return filepath.Clean(filepath.FromSlash(p))
}

// cleanWindowsPath is filepath.Clean for Windows paths, usable on any host.
func cleanWindowsPath(p string) string {
	p = strings.ReplaceAll(p, "/", `\`)
	if p == "" {
		return p
	}
	unc := strings.HasPrefix(p, `\\`)
	parts := strings.Split(p, `\`)
	out := make([]string, 0, len(parts))
	for i, part := range parts {
		if part == "" || part == "." {
			continue
		}
		if part == ".." {
			if len(out) == 0 {
				continue
			}
			// Do not climb past a drive root (C:).
			if len(out) == 1 && isWindowsDriveHead(out[0]) {
				continue
			}
			// Do not climb past the server/share pair of a UNC path.
			if unc && len(out) <= 2 {
				continue
			}
			out = out[:len(out)-1]
			continue
		}
		// Keep the drive letter as the first segment (C:).
		if i == 0 {
			out = append(out, part)
			continue
		}
		out = append(out, part)
	}
	if unc {
		if len(out) == 0 {
			return `\\`
		}
		return `\\` + strings.Join(out, `\`)
	}
	if len(out) == 0 {
		return ""
	}
	if len(out) == 1 && isWindowsDriveHead(out[0]) {
		return out[0] + `\`
	}
	return strings.Join(out, `\`)
}

func isWindowsDriveHead(s string) bool {
	return len(s) == 2 && unicode.IsLetter(rune(s[0])) && s[1] == ':'
}

// relUnderOS is relUnder with an explicit path OS, so a Windows root and an
// absolute Windows path can be compared on a Unix test host.
func relUnderOS(rootAbs, abs string, osName PathOS) (string, bool) {
	rootAbs = cleanPathOS(rootAbs, osName)
	abs = cleanPathOS(abs, osName)
	if osName == PathOSWindows {
		// Same drive-letter / UNC conventions as the Windows host.
		rootFold := strings.ToLower(rootAbs)
		absFold := strings.ToLower(abs)
		rel, ok := relUnderWindows(rootFold, absFold)
		if !ok {
			return "", false
		}
		// Return the relative path with the casing of abs, not the fold.
		_, relCased, ok := cutPrefixFold(abs, rootAbs)
		if !ok {
			return strings.ReplaceAll(rel, `\`, "/"), true
		}
		relCased = strings.TrimLeft(relCased, `/\`)
		if relCased == "" {
			return ".", true
		}
		return strings.ReplaceAll(relCased, `\`, "/"), true
	}
	if (osName == PathOSUnix) && runtime.GOOS != "windows" {
		return relUnder(rootAbs, abs)
	}
	rel, ok := relUnderUnix(rootAbs, abs)
	if !ok {
		return "", false
	}
	return rel, true
}

func cleanPathOS(p string, osName PathOS) string {
	if osName == PathOSWindows {
		return cleanWindowsPath(p)
	}
	return filepath.Clean(filepath.FromSlash(p))
}

func relUnderWindows(root, abs string) (string, bool) {
	root = strings.TrimRight(root, `\`)
	abs = strings.TrimRight(abs, `\`)
	if abs == root {
		return ".", true
	}
	prefix := root + `\`
	if !strings.HasPrefix(abs, prefix) {
		return "", false
	}
	rel := abs[len(prefix):]
	if rel == ".." || strings.HasPrefix(rel, `..\`) {
		return "", false
	}
	return rel, true
}

func relUnderUnix(root, abs string) (string, bool) {
	root = strings.TrimRight(root, "/")
	abs = strings.TrimRight(abs, "/")
	if abs == root {
		return ".", true
	}
	prefix := root + "/"
	if !strings.HasPrefix(abs, prefix) {
		return "", false
	}
	rel := abs[len(prefix):]
	if rel == ".." || strings.HasPrefix(rel, "../") {
		return "", false
	}
	return rel, true
}

// cutPrefixFold trims a case-insensitive prefix of root from abs and returns
// the remainder with abs's original casing.
func cutPrefixFold(abs, root string) (head, rest string, ok bool) {
	if len(abs) < len(root) {
		return "", "", false
	}
	if !strings.EqualFold(abs[:len(root)], root) {
		return "", "", false
	}
	return abs[:len(root)], abs[len(root):], true
}

// windowsPathToWSL converts a Windows path to the WSL mount form
// (C:\Users\foo -> /mnt/c/Users/foo). Paths that are already POSIX are
// returned with separators normalized and otherwise unchanged.
func windowsPathToWSL(p string) string {
	p = strings.TrimSpace(p)
	if p == "" {
		return p
	}
	// Already a WSL /mnt/<drive>/... path.
	if strings.HasPrefix(strings.ToLower(p), "/mnt/") && (len(p) == 6 || (len(p) > 6 && p[6] == '/')) {
		return strings.ReplaceAll(p, `\`, "/")
	}
	p = strings.ReplaceAll(p, `\`, "/")
	if isWindowsDriveAbs(p) || (len(p) == 2 && unicode.IsLetter(rune(p[0])) && p[1] == ':') {
		drive := strings.ToLower(string(p[0]))
		rest := ""
		if len(p) > 2 {
			rest = strings.TrimPrefix(p[2:], "/")
		}
		if rest == "" {
			return "/mnt/" + drive
		}
		return "/mnt/" + drive + "/" + rest
	}
	return p
}

// rewriteWindowsPathsForWSL rewrites drive-letter paths inside a command
// string so `wsl -e bash` sees /mnt/<drive>/... rather than C:\....
func rewriteWindowsPathsForWSL(command string) string {
	if command == "" {
		return command
	}
	var b strings.Builder
	b.Grow(len(command) + 8)
	i := 0
	for i < len(command) {
		if start, end, ok := windowsDrivePathAt(command, i); ok {
			b.WriteString(command[i:start])
			b.WriteString(windowsPathToWSL(command[start:end]))
			i = end
			continue
		}
		b.WriteByte(command[i])
		i++
	}
	return b.String()
}

// windowsDrivePathAt reports the next drive-letter path starting at or after
// i, but only when the match begins at i (the caller advances byte-wise).
func windowsDrivePathAt(s string, i int) (start, end int, ok bool) {
	if i+2 >= len(s) {
		return 0, 0, false
	}
	if !unicode.IsLetter(rune(s[i])) || s[i+1] != ':' {
		return 0, 0, false
	}
	if i+2 >= len(s) || (s[i+2] != '\\' && s[i+2] != '/') {
		return 0, 0, false
	}
	// Do not rewrite the middle of a larger token (e.g. a hash or label).
	if i > 0 {
		prev := s[i-1]
		if unicode.IsLetter(rune(prev)) || unicode.IsDigit(rune(prev)) || prev == '_' {
			return 0, 0, false
		}
	}
	j := i + 3
	for j < len(s) {
		c := s[j]
		if c == '"' || c == '\'' || c == ' ' || c == '\t' || c == '\n' ||
			c == '|' || c == '&' || c == '<' || c == '>' || c == '(' || c == ')' ||
			c == ';' {
			break
		}
		j++
	}
	return i, j, true
}
