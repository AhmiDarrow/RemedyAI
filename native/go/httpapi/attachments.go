package httpapi

import (
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"
)

var safeSIDRe = regexp.MustCompile(`[^A-Za-z0-9._-]`)

const maxTextInjectChars = 24_000

func safeSessionID(sessionID string) string {
	s := safeSIDRe.ReplaceAllString(strings.TrimSpace(sessionID), "_")
	if len(s) > 80 {
		s = s[:80]
	}
	if s == "" {
		s = "_"
	}
	return s
}

func attachmentsRoot(homeDir string) string {
	home := strings.TrimSpace(homeDir)
	if home == "" {
		userHome, err := os.UserHomeDir()
		if err != nil || userHome == "" {
			return ""
		}
		home = filepath.Join(userHome, ".remedy")
	}
	return filepath.Join(home, "attachments")
}

func sessionAttachmentsDir(sessionID, homeDir string) string {
	root := attachmentsRoot(homeDir)
	if root == "" {
		return ""
	}
	return filepath.Join(root, safeSessionID(sessionID))
}

func pathUnder(candidate, root string) bool {
	if root == "" || candidate == "" {
		return false
	}
	rel, err := filepath.Rel(root, candidate)
	if err != nil {
		return false
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return false
	}
	return true
}

func isPathUnderAttachments(path, homeDir, sessionID string) bool {
	raw := strings.TrimSpace(path)
	if raw == "" || strings.ContainsRune(raw, 0) {
		return false
	}
	candidate, err := filepath.Abs(raw)
	if err != nil {
		return false
	}
	candidate = filepath.Clean(candidate)
	roots := make([]string, 0, 2)
	if sessionID != "" {
		if d := sessionAttachmentsDir(sessionID, homeDir); d != "" {
			if abs, err := filepath.Abs(d); err == nil {
				roots = append(roots, abs)
			}
		}
		if strings.TrimSpace(homeDir) != "" {
			if d := sessionAttachmentsDir(sessionID, ""); d != "" {
				if abs, err := filepath.Abs(d); err == nil {
					roots = append(roots, abs)
				}
			}
		}
	} else if r := attachmentsRoot(homeDir); r != "" {
		if abs, err := filepath.Abs(r); err == nil {
			roots = append(roots, abs)
		}
	}
	for _, root := range roots {
		if pathUnder(candidate, root) {
			return true
		}
	}
	return false
}

func filterJailedAttachments(atts []map[string]any, homeDir, sessionID string) []map[string]any {
	out := make([]map[string]any, 0, len(atts))
	for _, a := range atts {
		if a == nil {
			continue
		}
		p, _ := a["path"].(string)
		if strings.TrimSpace(p) == "" {
			out = append(out, a)
			continue
		}
		if isPathUnderAttachments(p, homeDir, sessionID) {
			out = append(out, a)
		}
	}
	return out
}

func buildAttachmentPromptBlock(atts []map[string]any) string {
	if len(atts) == 0 {
		return ""
	}
	var b strings.Builder
	b.WriteString("\n---\nAttached files (saved for this session):\n")
	for _, a := range atts {
		name, _ := a["name"].(string)
		path, _ := a["path"].(string)
		mime, _ := a["mime"].(string)
		if name == "" {
			name = filepath.Base(path)
		}
		if name == "" {
			name = "file"
		}
		if mime == "" {
			mime = "unknown"
		}
		b.WriteString("- ")
		b.WriteString(name)
		b.WriteString(" (")
		b.WriteString(mime)
		b.WriteString(")")
		if path != "" {
			b.WriteString(": ")
			b.WriteString(path)
		}
		b.WriteByte('\n')
	}
	return b.String()
}

func injectTextFileSnippets(atts []map[string]any, homeDir, sessionID string) string {
	jailed := filterJailedAttachments(atts, homeDir, sessionID)
	var chunks strings.Builder
	total := 0
	for _, a := range jailed {
		if total >= maxTextInjectChars {
			chunks.WriteString(
				"\n…[additional text attachments omitted for context size; " +
					"use file_read on paths above]\n",
			)
			break
		}
		path, _ := a["path"].(string)
		if path == "" {
			continue
		}
		mime, _ := a["mime"].(string)
		name, _ := a["name"].(string)
		if name == "" {
			name = filepath.Base(path)
		}
		if !isProbablyText(mime, path) {
			continue
		}
		data, err := os.ReadFile(path)
		if err != nil {
			continue
		}
		if !utf8.Valid(data) {
			continue
		}
		text := string(data)
		remain := maxTextInjectChars - total
		if len(text) > remain {
			text = text[:remain] + "\n…[truncated]"
		}
		chunks.WriteString("\n---\nFile: ")
		chunks.WriteString(name)
		chunks.WriteString("\n```\n")
		chunks.WriteString(text)
		chunks.WriteString("\n```\n")
		total += len(text)
	}
	return chunks.String()
}

func isProbablyText(mime, path string) bool {
	m := strings.ToLower(strings.TrimSpace(mime))
	if strings.HasPrefix(m, "text/") || m == "application/json" ||
		m == "application/javascript" || m == "application/xml" {
		return true
	}
	ext := strings.ToLower(filepath.Ext(path))
	switch ext {
	case ".txt", ".md", ".py", ".go", ".ts", ".tsx", ".js", ".jsx",
		".json", ".yaml", ".yml", ".toml", ".csv", ".rs", ".zig",
		".c", ".h", ".cpp", ".hpp", ".java", ".kt", ".sh", ".ps1",
		".html", ".css", ".sql", ".xml":
		return true
	default:
		return false
	}
}
