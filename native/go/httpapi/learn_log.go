package httpapi

import (
	"bufio"
	"encoding/json"
	"os"
	"path/filepath"
	"strings"
	"sync"
	"time"
)

const (
	learnLogName     = "learn_log.jsonl"
	learnLogMaxBytes = 256 * 1024
	learnLogMaxLines = 400
)

var learnLogMu sync.Mutex

// learnLogEntry is one background learning run (turn_end, improve, or
// after_tool_batch). The same shape is written from Python and, on a
// timeout or IPC error, from Go.
type learnLogEntry struct {
	TS         string         `json:"ts"`
	Kind       string         `json:"kind"`
	DurationMS float64        `json:"duration_ms"`
	Timeout    bool           `json:"timeout,omitempty"`
	Error      string         `json:"error,omitempty"`
	OK         bool           `json:"ok"`
	Changed    map[string]any `json:"changed,omitempty"`
	Source     string         `json:"source,omitempty"`
}

func learnLogPath(home string) string {
	home = strings.TrimSpace(home)
	if home == "" {
		return ""
	}
	return filepath.Join(home, learnLogName)
}

func appendLearnLog(home, kind string, duration time.Duration, timeout bool, err error, changed map[string]any) {
	path := learnLogPath(home)
	if path == "" || strings.TrimSpace(kind) == "" {
		return
	}
	entry := learnLogEntry{
		TS:         time.Now().UTC().Format(time.RFC3339Nano),
		Kind:       kind,
		DurationMS: float64(duration.Microseconds()) / 1000.0,
		Timeout:    timeout,
		OK:         err == nil && !timeout,
		Changed:    changed,
		Source:     "go",
	}
	if err != nil {
		msg := strings.TrimSpace(err.Error())
		if len(msg) > 240 {
			msg = msg[:240]
		}
		entry.Error = msg
		entry.OK = false
	}
	raw, marshalErr := json.Marshal(entry)
	if marshalErr != nil {
		return
	}
	raw = append(raw, '\n')

	learnLogMu.Lock()
	defer learnLogMu.Unlock()
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return
	}
	f, openErr := os.OpenFile(path, os.O_APPEND|os.O_CREATE|os.O_WRONLY, 0o644)
	if openErr != nil {
		return
	}
	_, _ = f.Write(raw)
	_ = f.Close()
	rotateLearnLogLocked(path)
}

func learnLogCaps(path string) (maxBytes int64, maxLines int) {
	maxBytes, maxLines = int64(learnLogMaxBytes), learnLogMaxLines
	home := filepath.Dir(path)
	if home == "" || home == "." {
		return maxBytes, maxLines
	}
	cfg := LoadConfig(home)
	if n := resolveAdvancedInt(cfg, "learn_log_max_bytes"); n > 0 {
		maxBytes = int64(n)
	}
	if n := resolveAdvancedInt(cfg, "learn_log_max_lines"); n > 0 {
		maxLines = n
	}
	return maxBytes, maxLines
}

func rotateLearnLogLocked(path string) {
	info, err := os.Stat(path)
	if err != nil {
		return
	}
	maxBytes, maxLines := learnLogCaps(path)
	if info.Size() <= maxBytes {
		return
	}
	f, err := os.Open(path)
	if err != nil {
		return
	}
	var lines []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := sc.Text()
		if strings.TrimSpace(line) != "" {
			lines = append(lines, line)
		}
	}
	_ = f.Close()
	if len(lines) <= maxLines/2 {
		return
	}
	keep := lines
	if len(keep) > maxLines {
		keep = keep[len(keep)-maxLines:]
	} else {
		keep = keep[len(keep)/2:]
	}
	tmp := path + ".tmp"
	out, err := os.OpenFile(tmp, os.O_CREATE|os.O_TRUNC|os.O_WRONLY, 0o644)
	if err != nil {
		return
	}
	for _, line := range keep {
		_, _ = out.WriteString(line + "\n")
	}
	_ = out.Close()
	_ = os.Rename(tmp, path)
}

func readLearnLog(home string, limit int) []map[string]any {
	path := learnLogPath(home)
	if path == "" {
		return []map[string]any{}
	}
	if limit <= 0 {
		limit = 20
	}
	learnLogMu.Lock()
	defer learnLogMu.Unlock()
	f, err := os.Open(path)
	if err != nil {
		return []map[string]any{}
	}
	defer f.Close()
	var lines []string
	sc := bufio.NewScanner(f)
	sc.Buffer(make([]byte, 0, 64*1024), 1024*1024)
	for sc.Scan() {
		line := strings.TrimSpace(sc.Text())
		if line != "" {
			lines = append(lines, line)
		}
	}
	if len(lines) > limit {
		lines = lines[len(lines)-limit:]
	}
	out := make([]map[string]any, 0, len(lines))
	for _, line := range lines {
		var row map[string]any
		if json.Unmarshal([]byte(line), &row) != nil {
			continue
		}
		out = append(out, row)
	}
	return out
}
