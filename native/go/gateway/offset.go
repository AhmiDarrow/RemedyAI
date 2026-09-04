package gateway

import (
	"os"
	"path/filepath"
	"strconv"
	"strings"
)

// LoadUpdateOffset reads the persisted long-poll offset for a channel.
func LoadUpdateOffset(home, channel string) int {
	path := offsetPath(home, channel)
	raw, err := os.ReadFile(path)
	if err != nil {
		return 0
	}
	n, err := strconv.Atoi(strings.TrimSpace(string(raw)))
	if err != nil || n < 0 {
		return 0
	}
	return n
}

// SaveUpdateOffset persists a positive long-poll offset.
func SaveUpdateOffset(home, channel string, offset int) {
	if offset <= 0 {
		return
	}
	path := offsetPath(home, channel)
	_ = os.MkdirAll(filepath.Dir(path), 0o700)
	_ = os.WriteFile(path, []byte(strconv.Itoa(offset)+"\n"), 0o600)
}

func offsetPath(home, channel string) string {
	ch := normalizeID(channel)
	if ch == "" {
		ch = "telegram"
	}
	return filepath.Join(resolveHome(home), "locks", ch+"_offset.txt")
}

func resolveHome(home string) string {
	home = strings.TrimSpace(home)
	if home != "" {
		return home
	}
	if env := strings.TrimSpace(os.Getenv("REMEDY_HOME")); env != "" {
		return env
	}
	userHome, err := os.UserHomeDir()
	if err != nil || userHome == "" {
		return "."
	}
	return filepath.Join(userHome, ".remedy")
}
