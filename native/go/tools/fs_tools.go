package tools

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io/fs"
	"os"
	"path"
	"path/filepath"
	"regexp"
	"runtime"
	"sort"
	"strconv"
	"strings"
	"unicode/utf8"
)

// The frontier file surface: read / edit / write / glob / grep. These are the
// names and contracts a frontier model already knows; workspace.* stays
// registered for the CLI and older sessions but is hidden from the model.
//
// Every path resolves under the bound workspace root through os.Root, which
// gives the same guarantee filesystem.zig's resolve_beneath does: a symlink,
// junction or ".." inside the tree can never name a location outside it.
// The root itself is chosen by the runtime from the session, never by the
// model — a model-supplied workspace_root is overwritten before execution.

const (
	// fsMaxFileBytes mirrors filesystem.zig max_read_bytes: the largest file
	// these tools will pull into memory at once.
	fsMaxFileBytes = 16 << 20

	readMaxLines      = 2000
	readMaxBytes      = 256 << 10
	readMaxImageBytes = 8 << 20

	globMaxResults = 500

	grepDefaultResults = 200
	grepMaxResults     = 1000
	grepMaxPerFile     = 50
	grepMaxContext     = 10
	grepMaxFileBytes   = 2 << 20
	grepMaxLineBytes   = 512
)

// walkSkipDirs are never descended by glob or grep unless the caller's pattern
// names one explicitly. They are build and cache trees, not source.
var walkSkipDirs = []string{
	".git", "node_modules", "__pycache__", ".mypy_cache",
	".pytest_cache", ".ruff_cache", ".venv", ".gradle",
}

// RegisterFileTools installs the model-facing file tools. They are Go-native:
// no Python worker and no host library are involved, so they work in the CLI
// and in tests exactly as they do in a turn.
func RegisterFileTools(registry *Registry) error {
	if registry == nil {
		return fmt.Errorf("%w: nil registry", ErrInvalidDescriptor)
	}

	if err := registry.Register(Descriptor{
		ID:      "read",
		Version: 1,
		Description: "Read a text file with 1-based numbered lines, or an image file (png/jpeg/gif/webp) as an image. " +
			"Returns total_lines and next_offset so a long file can be paged; next_offset is 0 when the file ends. " +
			"Paths resolve under the workspace root.",
		Runtime: RuntimeGo,
		Risk:    RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path"],
			"properties":{
				"path":{"type":"string","minLength":1,"description":"File to read. Absolute (must be inside the workspace root) or relative to that root."},
				"offset":{"type":"integer","minimum":1,"description":"1-based line number to start from. Defaults to 1; pass next_offset from a previous call to continue."},
				"limit":{"type":"integer","minimum":1,"maximum":2000,"description":"Maximum lines to return (default and maximum 2000). The result is also capped at 256 KB."},
				` + propWorkspaceRoot + `
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","content","total_lines","offset","next_offset","truncated"],
			"properties":{
				"path":{"type":"string"},
				"content":{"type":"string"},
				"total_lines":{"type":"integer","minimum":0},
				"offset":{"type":"integer","minimum":0},
				"next_offset":{"type":"integer","minimum":0},
				"truncated":{"type":"boolean"},
				"bytes":{"type":"integer","minimum":0},
				"line_ending":{"type":"string"},
				"media_type":{"type":"string"},
				"is_image":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeRead)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:      "edit",
		Version: 1,
		Description: "Replace an exact string in a file. old_string must appear exactly once unless replace_all is set; " +
			"0 or several matches is an error that reports the count. BOM and CRLF/LF line endings are preserved byte-for-byte.",
		Runtime:      RuntimeGo,
		Risk:         RiskMutation,
		Capabilities: []string{"filesystem.write"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","old_string","new_string"],
			"properties":{
				"path":{"type":"string","minLength":1,"description":"File to edit. Absolute (must be inside the workspace root) or relative to that root. The file must already exist — use write to create one."},
				"old_string":{"type":"string","minLength":1,"description":"Exact text to replace, including indentation. Add surrounding lines until it is unique."},
				"new_string":{"type":"string","description":"Replacement text. Write it with \\n line breaks; a CRLF file gets CRLF back automatically."},
				"replace_all":{"type":"boolean","description":"Replace every occurrence instead of requiring exactly one. Default false."},
				` + propWorkspaceRoot + `
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","replacements","bytes"],
			"properties":{
				"path":{"type":"string"},
				"replacements":{"type":"integer","minimum":1},
				"bytes":{"type":"integer","minimum":0},
				"line_ending":{"type":"string"},
				"bom":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeEdit)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:           "write",
		Version:      1,
		Description:  "Create or overwrite a UTF-8 text file under the workspace root. Parent directories are created; the write is atomic.",
		Runtime:      RuntimeGo,
		Risk:         RiskMutation,
		Capabilities: []string{"filesystem.write"},
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","content"],
			"properties":{
				"path":{"type":"string","minLength":1,"description":"File to write. Absolute (must be inside the workspace root) or relative to that root."},
				"content":{"type":"string","description":"Full new file contents. This replaces the whole file — use edit for a targeted change."},
				` + propWorkspaceRoot + `
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["path","bytes","created"],
			"properties":{
				"path":{"type":"string"},
				"bytes":{"type":"integer","minimum":0},
				"created":{"type":"boolean"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeWrite)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:      "glob",
		Version: 1,
		Description: "Find files by glob pattern under the workspace root, newest first. " +
			"Use **/ for recursive matches (**/*.go); a pattern without ** only matches one directory level. Capped at 500 results.",
		Runtime: RuntimeGo,
		Risk:    RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["pattern"],
			"properties":{
				"pattern":{"type":"string","minLength":1,"description":"Slash-separated glob: ** any depth, * within a segment, ? one character, [abc] a class. Example: src/**/*.ts"},
				"path":{"type":"string","description":"Directory to search, absolute inside the workspace root or relative to it. Defaults to the root."},
				` + propWorkspaceRoot + `
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["files","count","truncated"],
			"properties":{
				"files":{"type":"array","items":{"type":"string"}},
				"count":{"type":"integer","minimum":0},
				"truncated":{"type":"boolean"},
				"searched":{"type":"string"}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeGlob)); err != nil {
		return err
	}

	if err := registry.Register(Descriptor{
		ID:      "grep",
		Version: 1,
		Description: "Search file contents with a Go/RE2 regular expression and optional context lines. " +
			"Set literal:true to search for the pattern as plain text. Reports truncated when a per-file or global cap is hit.",
		Runtime: RuntimeGo,
		Risk:    RiskReadOnly,
		InputSchema: json.RawMessage(`{
			"type":"object",
			"required":["pattern"],
			"properties":{
				"pattern":{"type":"string","minLength":1,"description":"Regular expression (Go/RE2 syntax). An invalid expression is an error, never a silent literal search."},
				"path":{"type":"string","description":"File or directory to search, absolute inside the workspace root or relative to it. Defaults to the root."},
				"glob":{"type":"string","description":"Only search files whose root-relative path matches this glob, e.g. **/*.go"},
				"context":{"type":"integer","minimum":0,"maximum":10,"description":"Lines of context to include before and after each match. Default 0."},
				"max_results":{"type":"integer","minimum":1,"maximum":1000,"description":"Global cap on returned matches (default 200). Each file also stops after 50 matches."},
				"literal":{"type":"boolean","description":"Treat pattern as plain text instead of a regular expression. Default false."},
				"case_insensitive":{"type":"boolean","description":"Match without regard to case. Default false."},
				` + propWorkspaceRoot + `
			},
			"additionalProperties":false
		}`),
		OutputSchema: json.RawMessage(`{
			"type":"object",
			"required":["matches","count","files_matched","files_searched","truncated"],
			"properties":{
				"matches":{
					"type":"array",
					"items":{
						"type":"object",
						"required":["path","line","text"],
						"properties":{
							"path":{"type":"string"},
							"line":{"type":"integer","minimum":1},
							"text":{"type":"string"},
							"before":{"type":"array","items":{"type":"string"}},
							"after":{"type":"array","items":{"type":"string"}}
						},
						"additionalProperties":false
					}
				},
				"count":{"type":"integer","minimum":0},
				"files_matched":{"type":"integer","minimum":0},
				"files_searched":{"type":"integer","minimum":0},
				"truncated":{"type":"boolean"},
				"truncated_files":{"type":"array","items":{"type":"string"}}
			},
			"additionalProperties":false
		}`),
	}, ExecutorFunc(executeGrep)); err != nil {
		return err
	}

	return nil
}

// ---------------------------------------------------------------------------
// Path binding
// ---------------------------------------------------------------------------

// boundRoot opens the session's workspace root. The caller never supplies it —
// the runtime overwrites workspace_root on every call before execution.
func boundRoot(raw string) (*os.Root, string, error) {
	root := strings.TrimSpace(raw)
	if root == "" {
		return nil, "", fmt.Errorf(
			"%w: no workspace root is bound for this turn, so file paths cannot be resolved. "+
				"Open a project folder, or use bash for work outside a project", ErrInvalidInput)
	}
	abs, err := filepath.Abs(root)
	if err != nil {
		return nil, "", fmt.Errorf("%w: workspace root %q is not usable: %v", ErrInvalidInput, root, err)
	}
	opened, err := os.OpenRoot(abs)
	if err != nil {
		return nil, abs, fmt.Errorf("%w: workspace root %q could not be opened: %v", ErrInvalidInput, abs, err)
	}
	return opened, abs, nil
}

// resolveBoundPath maps a model-supplied path to a slash-separated path
// relative to rootAbs. Absolute inputs must already be inside the root; the
// error says which root, because a model that is told the boundary can correct
// itself and one that is not will guess again.
func resolveBoundPath(rootAbs, p string) (string, error) {
	p = strings.TrimSpace(p)
	if p == "" {
		return "", fmt.Errorf("%w: path is required", ErrInvalidInput)
	}
	native := filepath.FromSlash(p)
	if filepath.IsAbs(native) {
		rel, ok := relUnder(rootAbs, filepath.Clean(native))
		if !ok {
			return "", outsideRootError(p, rootAbs)
		}
		return normalizeRel(rel, p, rootAbs)
	}
	if strings.HasPrefix(p, "/") || strings.HasPrefix(p, `\`) {
		return "", fmt.Errorf(
			"%w: %q starts at a filesystem root but is not an absolute path on this host. "+
				"Pass a path relative to %s, or a full absolute path inside it", ErrInvalidInput, p, rootAbs)
	}
	rel := filepath.Clean(native)
	return normalizeRel(rel, p, rootAbs)
}

func normalizeRel(rel, original, rootAbs string) (string, error) {
	if rel == "." || rel == "" {
		return ".", nil
	}
	if rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator)) {
		return "", outsideRootError(original, rootAbs)
	}
	return filepath.ToSlash(rel), nil
}

func outsideRootError(p, rootAbs string) error {
	return fmt.Errorf("%w: %q is outside the workspace root %s — pass a path inside that root", ErrInvalidInput, p, rootAbs)
}

// relUnder reports the path of abs relative to rootAbs when abs is inside it.
// Windows filenames are case-insensitive and a model rarely reproduces the
// owner's casing, so a case-folded retry is a correctness fix, not a leniency:
// the folded path names the same file, and os.Root still enforces the jail.
func relUnder(rootAbs, abs string) (string, bool) {
	if rel, err := filepath.Rel(rootAbs, abs); err == nil && !escapes(rel) {
		return rel, true
	}
	if runtime.GOOS == "windows" {
		if rel, err := filepath.Rel(strings.ToLower(rootAbs), strings.ToLower(abs)); err == nil && !escapes(rel) {
			return rel, true
		}
	}
	return "", false
}

func escapes(rel string) bool {
	return rel == ".." || strings.HasPrefix(rel, ".."+string(filepath.Separator))
}

// ---------------------------------------------------------------------------
// read
// ---------------------------------------------------------------------------

var imageMediaTypes = map[string]string{
	".png":  "image/png",
	".jpg":  "image/jpeg",
	".jpeg": "image/jpeg",
	".gif":  "image/gif",
	".webp": "image/webp",
}

func executeRead(_ context.Context, request Request) (Result, error) {
	var body struct {
		Path          string `json:"path"`
		Offset        int    `json:"offset"`
		Limit         int    `json:"limit"`
		WorkspaceRoot string `json:"workspace_root"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	root, rootAbs, err := boundRoot(body.WorkspaceRoot)
	if err != nil {
		return Result{}, err
	}
	defer root.Close()
	rel, err := resolveBoundPath(rootAbs, body.Path)
	if err != nil {
		return Result{}, err
	}
	info, err := root.Stat(rel)
	if err != nil {
		return Result{}, statError("read", body.Path, rootAbs, err)
	}
	if info.IsDir() {
		return Result{}, fmt.Errorf("%w: %q is a directory — use glob to list what is in it", ErrInvalidInput, body.Path)
	}
	shown := filepath.ToSlash(filepath.Join(rootAbs, rel))

	if media := imageMediaTypes[strings.ToLower(filepath.Ext(rel))]; media != "" {
		if info.Size() > readMaxImageBytes {
			return Result{}, fmt.Errorf("%w: %q is %d bytes, over the %d-byte image limit",
				ErrInvalidInput, body.Path, info.Size(), readMaxImageBytes)
		}
		data, err := root.ReadFile(rel)
		if err != nil {
			return Result{}, statError("read", body.Path, rootAbs, err)
		}
		out, err := json.Marshal(map[string]any{
			"path": shown, "content": "", "total_lines": 0, "offset": 0, "next_offset": 0,
			"truncated": false, "bytes": len(data), "media_type": media, "is_image": true,
		})
		if err != nil {
			return Result{}, err
		}
		return Result{Output: out, Images: []ImageResult{{MediaType: media, Data: data}}}, nil
	}

	if info.Size() > fsMaxFileBytes {
		return Result{}, fmt.Errorf("%w: %q is %d bytes, over the %d-byte read limit — use grep to find the part you need",
			ErrInvalidInput, body.Path, info.Size(), fsMaxFileBytes)
	}
	data, err := root.ReadFile(rel)
	if err != nil {
		return Result{}, statError("read", body.Path, rootAbs, err)
	}
	if !utf8.Valid(data) || bytes.IndexByte(data, 0) >= 0 {
		return Result{}, fmt.Errorf(
			"%w: %q is not UTF-8 text and is not an image read supports (png, jpeg, gif, webp)",
			ErrInvalidInput, body.Path)
	}

	lines := splitLines(string(data))
	total := len(lines)
	offset := body.Offset
	if offset == 0 {
		offset = 1
	}
	if offset > total && total > 0 {
		return Result{}, fmt.Errorf("%w: offset %d is past the last line of %q (total_lines %d)",
			ErrInvalidInput, offset, body.Path, total)
	}
	limit := body.Limit
	if limit <= 0 || limit > readMaxLines {
		limit = readMaxLines
	}

	var (
		content   strings.Builder
		last      = offset - 1
		truncated bool
	)
	for i := offset - 1; i < total; i++ {
		if i-(offset-1) >= limit {
			truncated = true
			break
		}
		line := strconv.Itoa(i+1) + "\t" + strings.TrimSuffix(lines[i], "\r") + "\n"
		if content.Len()+len(line) > readMaxBytes && content.Len() > 0 {
			truncated = true
			break
		}
		content.WriteString(line)
		last = i + 1
	}
	next := 0
	if truncated && last < total {
		next = last + 1
	}
	out, err := json.Marshal(map[string]any{
		"path":        shown,
		"content":     content.String(),
		"total_lines": total,
		"offset":      offset,
		"next_offset": next,
		"truncated":   truncated,
		"bytes":       len(data),
		"line_ending": lineEndingName(data),
	})
	return Result{Output: out}, err
}

// splitLines splits body into lines without inventing a trailing empty one for
// a file that ends in a newline.
func splitLines(body string) []string {
	if body == "" {
		return nil
	}
	lines := strings.Split(body, "\n")
	if n := len(lines); n > 0 && lines[n-1] == "" {
		lines = lines[:n-1]
	}
	return lines
}

func lineEndingName(data []byte) string {
	if bytes.Contains(data, []byte("\r\n")) {
		return "crlf"
	}
	return "lf"
}

func statError(op, shown, rootAbs string, err error) error {
	switch {
	case errors.Is(err, os.ErrNotExist):
		return fmt.Errorf("%w: %s: %q does not exist under %s — check the path with glob", ErrInvalidInput, op, shown, rootAbs)
	case errors.Is(err, os.ErrPermission):
		return fmt.Errorf("%w: %s: %q cannot be opened (permission denied)", ErrInvalidInput, op, shown)
	default:
		return fmt.Errorf("%w: %s %q: %v", ErrInvalidInput, op, shown, err)
	}
}

// ---------------------------------------------------------------------------
// edit / write
// ---------------------------------------------------------------------------

var utf8BOM = []byte{0xEF, 0xBB, 0xBF}

func executeEdit(_ context.Context, request Request) (Result, error) {
	var body struct {
		Path          string `json:"path"`
		OldString     string `json:"old_string"`
		NewString     string `json:"new_string"`
		ReplaceAll    bool   `json:"replace_all"`
		WorkspaceRoot string `json:"workspace_root"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	if body.OldString == "" {
		return Result{}, fmt.Errorf("%w: old_string is empty — use write to create or replace a whole file", ErrInvalidInput)
	}
	if body.OldString == body.NewString {
		return Result{}, fmt.Errorf("%w: old_string and new_string are identical, so this edit would change nothing", ErrInvalidInput)
	}
	root, rootAbs, err := boundRoot(body.WorkspaceRoot)
	if err != nil {
		return Result{}, err
	}
	defer root.Close()
	rel, err := resolveBoundPath(rootAbs, body.Path)
	if err != nil {
		return Result{}, err
	}
	data, err := root.ReadFile(rel)
	if err != nil {
		return Result{}, statError("edit", body.Path, rootAbs, err)
	}
	if !utf8.Valid(data) {
		return Result{}, fmt.Errorf("%w: %q is not UTF-8 text, so an exact-string edit cannot be applied", ErrInvalidInput, body.Path)
	}

	bom := bytes.HasPrefix(data, utf8BOM)
	payload := data
	if bom {
		payload = data[len(utf8BOM):]
	}
	crlf := bytes.Contains(payload, []byte("\r\n"))
	oldStr, newStr := body.OldString, body.NewString
	if crlf {
		// The model writes \n; the file is \r\n. Matching and the replacement
		// both move to the file's own ending so a one-line edit stays one line
		// in the diff instead of rewriting every line in the file.
		oldStr, newStr = toCRLF(oldStr), toCRLF(newStr)
	}

	text := string(payload)
	count := strings.Count(text, oldStr)
	switch {
	case count == 0:
		return Result{}, fmt.Errorf(
			"%w: old_string was not found in %q (0 matches). Read the file and copy the exact text, "+
				"including indentation and line breaks", ErrInvalidInput, body.Path)
	case count > 1 && !body.ReplaceAll:
		return Result{}, fmt.Errorf(
			"%w: old_string matches %d times in %q. Add surrounding lines until it is unique, "+
				"or set replace_all:true to change all %d", ErrInvalidInput, count, body.Path, count)
	}

	replacements := 1
	if body.ReplaceAll {
		replacements = count
		text = strings.ReplaceAll(text, oldStr, newStr)
	} else {
		text = strings.Replace(text, oldStr, newStr, 1)
	}
	out := []byte(text)
	if bom {
		out = append(append([]byte(nil), utf8BOM...), out...)
	}
	if err := atomicWriteInRoot(root, rel, out); err != nil {
		return Result{}, err
	}
	payloadOut, err := json.Marshal(map[string]any{
		"path":         filepath.ToSlash(filepath.Join(rootAbs, rel)),
		"replacements": replacements,
		"bytes":        len(out),
		"line_ending":  lineEndingName(out),
		"bom":          bom,
	})
	return Result{Output: payloadOut}, err
}

// toCRLF rewrites every line ending in s to CRLF, whatever it started as.
func toCRLF(s string) string {
	return strings.ReplaceAll(strings.ReplaceAll(s, "\r\n", "\n"), "\n", "\r\n")
}

func executeWrite(_ context.Context, request Request) (Result, error) {
	var body struct {
		Path          string `json:"path"`
		Content       string `json:"content"`
		WorkspaceRoot string `json:"workspace_root"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	root, rootAbs, err := boundRoot(body.WorkspaceRoot)
	if err != nil {
		return Result{}, err
	}
	defer root.Close()
	rel, err := resolveBoundPath(rootAbs, body.Path)
	if err != nil {
		return Result{}, err
	}
	if rel == "." {
		return Result{}, fmt.Errorf("%w: path names the workspace root, not a file", ErrInvalidInput)
	}
	created := true
	if info, err := root.Stat(rel); err == nil {
		if info.IsDir() {
			return Result{}, fmt.Errorf("%w: %q is a directory", ErrInvalidInput, body.Path)
		}
		created = false
	}
	if parent := path.Dir(rel); parent != "." && parent != "" {
		if err := root.MkdirAll(parent, 0o755); err != nil {
			return Result{}, fmt.Errorf("%w: could not create %q under %s: %v", ErrInvalidInput, parent, rootAbs, err)
		}
	}
	data := []byte(body.Content)
	if err := atomicWriteInRoot(root, rel, data); err != nil {
		return Result{}, err
	}
	out, err := json.Marshal(map[string]any{
		"path":    filepath.ToSlash(filepath.Join(rootAbs, rel)),
		"bytes":   len(data),
		"created": created,
	})
	return Result{Output: out}, err
}

// atomicWriteInRoot writes through a sibling temp file and renames over the
// target, so a crash or a concurrent reader never sees a half-written file.
// Both names stay inside the root, so the jail applies to the temp file too.
func atomicWriteInRoot(root *os.Root, rel string, data []byte) error {
	dir := path.Dir(rel)
	if dir == "." {
		dir = ""
	}
	base := path.Base(rel)
	var tmp string
	var file *os.File
	for attempt := 0; attempt < 8; attempt++ {
		name := fmt.Sprintf(".%s.remedy-%d.tmp", base, os.Getpid()+attempt*7919)
		if dir != "" {
			name = dir + "/" + name
		}
		f, err := root.OpenFile(name, os.O_WRONLY|os.O_CREATE|os.O_EXCL, 0o644)
		if err == nil {
			tmp, file = name, f
			break
		}
		if !errors.Is(err, os.ErrExist) {
			return fmt.Errorf("%w: could not create a temporary file beside %q: %v", ErrInvalidInput, rel, err)
		}
	}
	if file == nil {
		return fmt.Errorf("%w: could not create a temporary file beside %q", ErrInvalidInput, rel)
	}
	writeErr := func() error {
		defer file.Close()
		if _, err := file.Write(data); err != nil {
			return err
		}
		return file.Sync()
	}()
	if writeErr != nil {
		_ = root.Remove(tmp)
		return fmt.Errorf("%w: writing %q failed: %v", ErrInvalidInput, rel, writeErr)
	}
	if err := root.Rename(tmp, rel); err != nil {
		_ = root.Remove(tmp)
		return fmt.Errorf("%w: replacing %q failed: %v", ErrInvalidInput, rel, err)
	}
	return nil
}

// ---------------------------------------------------------------------------
// glob
// ---------------------------------------------------------------------------

func executeGlob(ctx context.Context, request Request) (Result, error) {
	var body struct {
		Pattern       string `json:"pattern"`
		Path          string `json:"path"`
		WorkspaceRoot string `json:"workspace_root"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	pattern := strings.TrimSpace(body.Pattern)
	if pattern == "" {
		return Result{}, fmt.Errorf("%w: pattern is required, for example **/*.go", ErrInvalidInput)
	}
	root, rootAbs, err := boundRoot(body.WorkspaceRoot)
	if err != nil {
		return Result{}, err
	}
	defer root.Close()
	base, err := searchBase(root, rootAbs, body.Path)
	if err != nil {
		return Result{}, err
	}

	type hit struct {
		rel   string
		mtime int64
	}
	hits := make([]hit, 0, 64)
	truncated := false
	err = walkBound(ctx, root, base, pattern, func(rel string, d fs.DirEntry) error {
		if !matchGlob(pattern, relativeTo(base, rel)) {
			return nil
		}
		if len(hits) >= globMaxResults {
			truncated = true
			return fs.SkipAll
		}
		var mtime int64
		if info, err := d.Info(); err == nil {
			mtime = info.ModTime().UnixNano()
		}
		hits = append(hits, hit{rel: rel, mtime: mtime})
		return nil
	})
	if err != nil {
		return Result{}, err
	}
	sort.SliceStable(hits, func(i, j int) bool {
		if hits[i].mtime != hits[j].mtime {
			return hits[i].mtime > hits[j].mtime
		}
		return hits[i].rel < hits[j].rel
	})
	files := make([]string, 0, len(hits))
	for _, h := range hits {
		files = append(files, filepath.ToSlash(filepath.Join(rootAbs, h.rel)))
	}
	out, err := json.Marshal(map[string]any{
		"files":     files,
		"count":     len(files),
		"truncated": truncated,
		"searched":  filepath.ToSlash(filepath.Join(rootAbs, base)),
	})
	return Result{Output: out}, err
}

// searchBase resolves the optional path argument of glob/grep to a directory
// or file relative to the root.
func searchBase(root *os.Root, rootAbs, p string) (string, error) {
	if strings.TrimSpace(p) == "" {
		return ".", nil
	}
	rel, err := resolveBoundPath(rootAbs, p)
	if err != nil {
		return "", err
	}
	if _, err := root.Stat(rel); err != nil {
		return "", statError("search", p, rootAbs, err)
	}
	return rel, nil
}

// relativeTo returns rel expressed against base, so a pattern is matched
// against the part of the path the caller actually asked about.
func relativeTo(base, rel string) string {
	if base == "." || base == "" {
		return rel
	}
	if rel == base {
		return path.Base(rel)
	}
	return strings.TrimPrefix(rel, base+"/")
}

// walkBound walks base inside root, skipping build and cache trees unless the
// caller's pattern names one. visit receives slash paths relative to the root.
func walkBound(ctx context.Context, root *os.Root, base, pattern string, visit func(string, fs.DirEntry) error) error {
	fsys := root.FS()
	err := fs.WalkDir(fsys, base, func(p string, d fs.DirEntry, err error) error {
		if err != nil {
			// An unreadable subtree is not a reason to fail the whole search.
			if d != nil && d.IsDir() {
				return fs.SkipDir
			}
			return nil
		}
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if d.IsDir() {
			if p != base && skipWalkDir(d.Name(), pattern) {
				return fs.SkipDir
			}
			return nil
		}
		if !d.Type().IsRegular() {
			return nil
		}
		return visit(p, d)
	})
	if err != nil && !errors.Is(err, fs.SkipAll) {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		return fmt.Errorf("%w: walking %q: %v", ErrInvalidInput, base, err)
	}
	return nil
}

func skipWalkDir(name, pattern string) bool {
	for _, skip := range walkSkipDirs {
		if name == skip {
			return !strings.Contains(pattern, skip)
		}
	}
	return false
}

// matchGlob matches a slash path against a pattern with ** (any depth),
// * (within a segment), ? and [class]. Windows paths are matched case-folded.
func matchGlob(pattern, name string) bool {
	if runtime.GOOS == "windows" {
		pattern, name = strings.ToLower(pattern), strings.ToLower(name)
	}
	return matchSegments(strings.Split(path.Clean(pattern), "/"), strings.Split(path.Clean(name), "/"))
}

func matchSegments(pattern, name []string) bool {
	for len(pattern) > 0 {
		if pattern[0] == "**" {
			// ** absorbs zero or more segments; try each split point.
			rest := pattern[1:]
			if len(rest) == 0 {
				return true
			}
			for i := 0; i <= len(name); i++ {
				if matchSegments(rest, name[i:]) {
					return true
				}
			}
			return false
		}
		if len(name) == 0 {
			return false
		}
		ok, err := path.Match(pattern[0], name[0])
		if err != nil || !ok {
			return false
		}
		pattern, name = pattern[1:], name[1:]
	}
	return len(name) == 0
}

// ---------------------------------------------------------------------------
// grep
// ---------------------------------------------------------------------------

func executeGrep(ctx context.Context, request Request) (Result, error) {
	var body struct {
		Pattern       string `json:"pattern"`
		Path          string `json:"path"`
		Glob          string `json:"glob"`
		Context       int    `json:"context"`
		MaxResults    int    `json:"max_results"`
		Literal       bool   `json:"literal"`
		CaseInsens    bool   `json:"case_insensitive"`
		WorkspaceRoot string `json:"workspace_root"`
	}
	if err := json.Unmarshal(request.Input, &body); err != nil {
		return Result{}, fmt.Errorf("%w: %v", ErrInvalidInput, err)
	}
	if strings.TrimSpace(body.Pattern) == "" {
		return Result{}, fmt.Errorf("%w: pattern is required", ErrInvalidInput)
	}
	expr := body.Pattern
	if body.Literal {
		expr = regexp.QuoteMeta(expr)
	}
	if body.CaseInsens {
		expr = "(?i)" + expr
	}
	re, err := regexp.Compile(expr)
	if err != nil {
		return Result{}, fmt.Errorf(
			"%w: pattern is not a valid regular expression: %v. Fix the expression, or set literal:true to search for it as plain text",
			ErrInvalidInput, err)
	}
	around := body.Context
	if around < 0 {
		around = 0
	}
	if around > grepMaxContext {
		around = grepMaxContext
	}
	limit := body.MaxResults
	if limit <= 0 {
		limit = grepDefaultResults
	}
	if limit > grepMaxResults {
		limit = grepMaxResults
	}

	root, rootAbs, err := boundRoot(body.WorkspaceRoot)
	if err != nil {
		return Result{}, err
	}
	defer root.Close()
	base, err := searchBase(root, rootAbs, body.Path)
	if err != nil {
		return Result{}, err
	}
	fileGlob := strings.TrimSpace(body.Glob)

	matches := make([]map[string]any, 0, 32)
	truncatedFiles := make([]string, 0, 4)
	filesSearched, filesMatched := 0, 0
	truncated := false

	search := func(rel string) error {
		if ctx.Err() != nil {
			return ctx.Err()
		}
		if fileGlob != "" && !matchGlob(fileGlob, relativeTo(base, rel)) {
			return nil
		}
		info, err := root.Stat(rel)
		if err != nil || info.Size() > grepMaxFileBytes {
			return nil
		}
		data, err := root.ReadFile(rel)
		if err != nil || !utf8.Valid(data) || bytes.IndexByte(data, 0) >= 0 {
			return nil
		}
		filesSearched++
		lines := splitLines(string(data))
		shown := filepath.ToSlash(filepath.Join(rootAbs, rel))
		inFile := 0
		for i, line := range lines {
			if !re.MatchString(line) {
				continue
			}
			if len(matches) >= limit {
				truncated = true
				return fs.SkipAll
			}
			if inFile >= grepMaxPerFile {
				truncated = true
				truncatedFiles = append(truncatedFiles, shown)
				break
			}
			match := map[string]any{"path": shown, "line": i + 1, "text": clipLine(line)}
			if around > 0 {
				match["before"] = clipLines(lines[maxInt(0, i-around):i])
				match["after"] = clipLines(lines[minInt(len(lines), i+1):minInt(len(lines), i+1+around)])
			}
			matches = append(matches, match)
			inFile++
		}
		if inFile > 0 {
			filesMatched++
		}
		return nil
	}

	if info, err := root.Stat(base); err == nil && !info.IsDir() {
		if err := search(base); err != nil && !errors.Is(err, fs.SkipAll) {
			return Result{}, err
		}
	} else if err := walkBound(ctx, root, base, orPattern(fileGlob, "**"), func(rel string, _ fs.DirEntry) error {
		return search(rel)
	}); err != nil {
		return Result{}, err
	}

	out, err := json.Marshal(map[string]any{
		"matches":         matches,
		"count":           len(matches),
		"files_matched":   filesMatched,
		"files_searched":  filesSearched,
		"truncated":       truncated,
		"truncated_files": truncatedFiles,
	})
	return Result{Output: out}, err
}

func orPattern(primary, fallback string) string {
	if strings.TrimSpace(primary) != "" {
		return primary
	}
	return fallback
}

// clipLine bounds one reported line so a minified file cannot flood the
// transcript. Truncation never splits a UTF-8 sequence.
func clipLine(s string) string {
	s = strings.TrimSuffix(s, "\r")
	if len(s) <= grepMaxLineBytes {
		return s
	}
	cut := grepMaxLineBytes
	for cut > 0 && !utf8.RuneStart(s[cut]) {
		cut--
	}
	return s[:cut] + "…"
}

func clipLines(in []string) []string {
	out := make([]string, 0, len(in))
	for _, s := range in {
		out = append(out, clipLine(s))
	}
	return out
}

func maxInt(a, b int) int {
	if a > b {
		return a
	}
	return b
}

func minInt(a, b int) int {
	if a < b {
		return a
	}
	return b
}
