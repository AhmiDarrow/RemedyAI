package httpapi

import (
	"crypto/rand"
	"encoding/hex"
	"fmt"
	"mime"
	"os"
	"path/filepath"
	"regexp"
	"strings"
	"unicode/utf8"
)

var (
	safeSIDRe  = regexp.MustCompile(`[^A-Za-z0-9._-]`)
	safeNameRe = regexp.MustCompile(`[^A-Za-z0-9._\- ()\[\]]+`)
)

const (
	maxTextInjectChars  = 24_000
	maxAttachmentBytes  = 15 * 1024 * 1024
)

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

func isProbablyText(mimeType, path string) bool {
	m := strings.ToLower(strings.TrimSpace(mimeType))
	if strings.HasPrefix(m, "text/") || m == "application/json" ||
		m == "application/javascript" || m == "application/xml" ||
		m == "application/x-yaml" || m == "application/yaml" ||
		m == "application/typescript" {
		return true
	}
	ext := strings.ToLower(filepath.Ext(path))
	switch ext {
	case ".txt", ".md", ".py", ".go", ".ts", ".tsx", ".js", ".jsx",
		".json", ".yaml", ".yml", ".toml", ".csv", ".rs", ".zig",
		".c", ".h", ".cpp", ".hpp", ".java", ".kt", ".sh", ".ps1",
		".html", ".css", ".sql", ".xml", ".log", ".ini", ".cfg",
		".env", ".vue", ".svelte", ".scss":
		return true
	default:
		return false
	}
}

func isImageMIME(mimeType string) bool {
	m := strings.ToLower(strings.TrimSpace(mimeType))
	if strings.HasPrefix(m, "image/") {
		return true
	}
	switch m {
	case "image/png", "image/jpeg", "image/jpg", "image/gif",
		"image/webp", "image/bmp":
		return true
	default:
		return false
	}
}

func sanitizeFilename(name string) string {
	base := filepath.Base(strings.TrimSpace(name))
	if base == "" || base == "." || base == ".." {
		base = "file"
	}
	base = safeNameRe.ReplaceAllString(base, "_")
	base = strings.Trim(base, "._")
	if base == "" {
		base = "file"
	}
	if len(base) > 180 {
		ext := filepath.Ext(base)
		if len(ext) > 40 {
			ext = ext[:40]
		}
		stem := strings.TrimSuffix(base, filepath.Ext(base))
		if len(stem) > 140 {
			stem = stem[:140]
		}
		base = stem + ext
	}
	return base
}

func guessMIME(filename, declared string) string {
	d := strings.TrimSpace(declared)
	if d != "" && !strings.EqualFold(d, "application/octet-stream") {
		return d
	}
	if m := mime.TypeByExtension(filepath.Ext(filename)); m != "" {
		return m
	}
	return "application/octet-stream"
}

func newAttachmentID() string {
	var b [6]byte
	_, _ = rand.Read(b[:])
	return hex.EncodeToString(b[:])
}

// saveUpload writes bytes under the session attachments jail and returns meta
// matching Python interfaces.attachments.save_upload / desktop AttachmentMeta.
func saveUpload(sessionID, filename string, data []byte, contentType, homeDir string) (map[string]any, error) {
	if len(data) > maxAttachmentBytes {
		return nil, fmt.Errorf(
			"File too large (%d bytes). Max is %d MB.",
			len(data), maxAttachmentBytes/(1024*1024),
		)
	}
	directory := sessionAttachmentsDir(sessionID, homeDir)
	if directory == "" {
		return nil, fmt.Errorf("attachments directory unavailable")
	}
	if err := os.MkdirAll(directory, 0o755); err != nil {
		return nil, err
	}
	displayName := sanitizeFilename(filename)
	path := filepath.Join(directory, displayName)
	if err := os.WriteFile(path, data, 0o644); err != nil {
		return nil, err
	}
	abs, err := filepath.Abs(path)
	if err != nil {
		abs = path
	}
	mimeType := guessMIME(displayName, contentType)
	return map[string]any{
		"id":       newAttachmentID(),
		"name":     displayName,
		"path":     abs,
		"mime":     mimeType,
		"size":     len(data),
		"is_image": isImageMIME(mimeType),
		"is_text":  isProbablyText(mimeType, displayName),
	}, nil
}

// resolveAttachmentFile returns a jailed absolute path for a basename under the
// session attachments dir. ok=false with notFound distinguishes 404 vs 400.
func resolveAttachmentFile(sessionID, filename, homeDir string) (path string, notFound bool, err error) {
	safe := filepath.Base(strings.TrimSpace(filename))
	if safe == "" || safe == "." || safe == ".." || strings.ContainsAny(safe, `/\`) {
		return "", false, fmt.Errorf("Invalid path")
	}
	directory := sessionAttachmentsDir(sessionID, homeDir)
	if directory == "" {
		return "", false, fmt.Errorf("Invalid path")
	}
	root, absErr := filepath.Abs(directory)
	if absErr != nil {
		return "", false, fmt.Errorf("Invalid path")
	}
	candidate, absErr := filepath.Abs(filepath.Join(directory, safe))
	if absErr != nil || !pathUnder(candidate, root) {
		return "", false, fmt.Errorf("Invalid path")
	}
	st, statErr := os.Stat(candidate)
	if statErr != nil || st.IsDir() {
		return "", true, fmt.Errorf("Attachment not found")
	}
	return candidate, false, nil
}
