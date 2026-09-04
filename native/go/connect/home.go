package connect

import (
	"os"
	"path/filepath"
	"strings"
)

// ResolveHome returns the Remedy home directory.
// Explicit home wins; otherwise REMEDY_HOME; otherwise ~/.remedy.
func ResolveHome(home string) string {
	home = strings.TrimSpace(home)
	if home != "" {
		return home
	}
	env := strings.TrimSpace(strings.Trim(os.Getenv("REMEDY_HOME"), "\"'"))
	if env != "" {
		return env
	}
	userHome, err := os.UserHomeDir()
	if err != nil || userHome == "" {
		return ".remedy"
	}
	return filepath.Join(userHome, ".remedy")
}

// ConnectRoot is ~/.remedy/auth/connect (creates the directory).
func ConnectRoot(home string) (string, error) {
	root := filepath.Join(ResolveHome(home), "auth", "connect")
	if err := os.MkdirAll(root, 0o700); err != nil {
		return "", err
	}
	_ = os.Chmod(root, 0o700)
	return root, nil
}
