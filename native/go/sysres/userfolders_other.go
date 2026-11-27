//go:build !windows

package sysres

import (
	"os"
	"path/filepath"
	"runtime"
	"strings"
)

var xdgKey = [...]string{"XDG_DESKTOP_DIR", "XDG_DOCUMENTS_DIR", "XDG_DOWNLOAD_DIR"}

// systemFolder reads the folder from user-dirs.dirs. macOS keeps these
// folders at fixed names under the home, so it has nothing to read.
func systemFolder(kind FolderKind, home string) string {
	if runtime.GOOS == "darwin" {
		return ""
	}
	conf := filepath.Join(home, ".config")
	if v := os.Getenv("XDG_CONFIG_HOME"); v != "" && isOwnHome(home) {
		conf = v
	}
	data, err := os.ReadFile(filepath.Join(conf, "user-dirs.dirs"))
	if err != nil {
		return ""
	}
	return xdgUserDir(string(data), xdgKey[kind], home)
}

// xdgUserDir reads one KEY="$HOME/Name" line. A folder set to the home
// itself means "none" in that file.
func xdgUserDir(text, key, home string) string {
	for _, line := range strings.Split(text, "\n") {
		name, value, ok := strings.Cut(strings.TrimSpace(line), "=")
		if !ok || strings.TrimSpace(name) != key {
			continue
		}
		value = strings.Trim(strings.TrimSpace(value), `"`)
		if rest, ok := strings.CutPrefix(value, "$HOME"); ok {
			value = filepath.Join(home, rest)
		}
		if !filepath.IsAbs(value) || filepath.Clean(value) == filepath.Clean(home) {
			return ""
		}
		return filepath.Clean(value)
	}
	return ""
}
